from textual import work
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll, Container, Vertical
from textual.widgets import Input, Markdown, RichLog, Rule, Label, TabbedContent, TabPane, DataTable
from textual.worker import Worker, get_current_worker
from textual.binding import Binding
from rich.markup import escape
from ...ax25.frame import *
from ...ax25.abm import *
from ...transport.kiss import *
import sys

class ClientApp(App):
    CSS_PATH = "client.tcss"
    BINDINGS = [
        Binding("ctrl+c", "ctrl_c", "Disconnect & Quit", show=False, priority=True),
        Binding("ctrl+z", "quit", "Force-Quit", show=False, priority=True),
        Binding("ctrl+d", "disconnect", "Disconnect", show=False, priority=True),
        Binding("tab", "focus_next", "Focus Next", show=False),
        Binding("shift+tab", "focus_previous", "Focus Previous", show=False),
    ]

    def __init__(self, session):
        App.__init__(self)
        self.output_text = ''
        self.log_text = ''
        self.packets = []
        self.session = session
        self.session.port.on_tx = self.on_port_tx
        self.session.port.on_rx = self.on_port_rx
        self.quit_on_disconnect = False
        self.session_t_zero = time.time()

    def compose(self) -> ComposeResult:
        # with Vertical():
        with TabbedContent():
            with TabPane("Session"):
                with VerticalScroll(id="results-container", classes='scroll-container'):
                    yield Label(id="results", classes='scroll-body')
            with TabPane("Packets"):
                with VerticalScroll(id="packets-container", classes='scroll-container'):
                    yield DataTable(id="packets", classes='scroll-body')
            with TabPane("Diagnostics"):
                yield Label(id="diagnostics", classes='scroll-body')
                with VerticalScroll(id="log-container", classes='scroll-container'):
                    yield Label(id="log", classes='scroll-body')

        with Container(id="bottom-container"):
            yield Markdown(id="status")
            yield Rule(line_style='double')
            yield Input(id="input", placeholder="Input...")

    def on_mount(self) -> None:
        self.query_one(Input).focus()
        self.query_one('#packets').add_columns(
            'Timestamp',
            'Source',
            'Dest',
            'Dir',
            'T',
            'Control',
            'N(S)',
            'N(R)',
            'PF',
            'Body'
        )
        self.on_abm_state_change()
        self.background_processing()

    def on_input_submitted(self, message: Input.Changed) -> None:
        b = message.value.encode('utf-8', 'backslashreplace') + b'\r'
        self.session.stream_outgoing += b
        self.on_abm_rx(b, from_me=True)
        self.query_one(Input).value = ''

    def on_abm_rx(self, message, from_me=False):
        t = escape(message.decode('utf-8', 'backslashreplace').replace('\r', '\n'))
        if from_me:
            t = '[b]' + t + '[/]'
        self.output_text += t
        self.query_one('#results').update(self.output_text)
        self.query_one('#results-container').scroll_end()

    def on_port_rx(self, frame):
        f = parse_ax25_frame(frame, 8)
        if f.source.same_station(self.session.mycall):
            # Crosstalk echo of my own packet
            # TODO: Better solution
            return
        self.add_packet(True, f)

    def on_port_tx(self, frame):
        self.add_packet(False, parse_ax25_frame(frame, 8))

    def add_packet(self, is_rx, frame):
        if is_rx:
            if frame.source.same_station(self.session.theircall):
                dir_pre = '[blue]'
            else:
                dir_pre = '[grey46]'
        else:
            dir_pre = '[green]'

        cc = (frame.source.c, frame.dest.c)

        row = [
            f"{time.time() - self.session_t_zero:.2f}",
            dir_pre + str(frame.source) + "[/]",
            dir_pre + str(frame.dest) + "[/]",
            "cmd" if cc==(0,1) else ("rsp" if cc==(1,0) else "?"+str(cc)),
            str(frame.frametype),
            frame.control.ss.name if frame.frametype=='S' else (frame.control.mmmmm.name if frame.frametype=='U' else ''),
            str(frame.control.ns) if frame.frametype == 'I' else '',
            str(frame.control.nr) if frame.frametype != 'U' else '',
            str(frame.control.pf),
            frame.data.decode('utf-8', 'backslashreplace').replace('\r', '\\r').replace('\n', '\\n')
        ]

        if dir_pre == '[grey46]':
            row = [dir_pre + x + '[/]' for x in row]

        self.query_one('#packets').add_row(*row)

    def on_abm_state_change(self):
        self.query_one("#status").update(self.session.state.name + (" (quitting...)" if self.quit_on_disconnect else ''))
        if self.session.state == AX25ConnectedModeConnection.States.DISCONNECTED:
            self.query_one('#input').disabled = True
            if self.quit_on_disconnect:
                self.exit(0)

    def on_periodic_poll(self):
        def str_timer(timer):
            if timer.expired:
                return f"[red]{timer.elapsed:.1f}[/]/{timer.timeout:.1f}"
            elif timer.running:
                return f"[green]{timer.elapsed:.1f}[/]/{timer.timeout:.1f}"
            else:
                return f"[grey46]STOP[/]/{timer.timeout:.1f}"

        self.query_one('#diagnostics').update("\n".join([
            f"V(S) = {self.session.vs}, V(R) = {self.session.vr}, V(A) = {self.session.va}",
            f"Retransmit timer: {str_timer(self.session.retransmit_timer)}",
            f"Keepalive timer: {str_timer(self.session.keepalive_timer)}",
            f"Burst ACK timer: {str_timer(self.session.burst_recieve_timer)}",
            f"Pending frame: {self.session.pending_ack_frame}",
            f"Outgoing Stream: {self.session.stream_outgoing}"
        ]))

    def on_session_log(self, a):
        message = ' '.join(map(str, a)) + "\n"
        self.log_text += message
        self.query_one('#log').update(self.log_text)
        self.query_one('#log-container').scroll_end()

    def action_ctrl_c(self):
        if self.session.state == AX25ConnectedModeConnection.States.DISCONNECTED:
            self.exit(0)
        else:
            self.session.initiate_disconnection()
            self.quit_on_disconnect = True

    def action_disconnect(self):
        self.session.initiate_disconnection()

    @work(exclusive=True, thread=True)
    def background_processing(self):
        def log(*a, **k):
            assert k == {}
            self.call_from_thread(self.on_session_log, a)
        self.session.debug_print = log
        time.sleep(0.1)
        while True:
            prev_state = self.session.state
            self.session.poll()

            if self.session.stream_incoming:
                self.call_from_thread(self.on_abm_rx, self.session.stream_incoming)
                self.session.stream_incoming = b''

            time.sleep(0.05)

            if self.session.state != prev_state:
                self.call_from_thread(self.on_abm_state_change)

            self.call_from_thread(self.on_periodic_poll)

            if get_current_worker().is_cancelled:
                break


def run_ui(session):
    app = ClientApp(session)
    app.run()
    sys.exit(0)
