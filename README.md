# tncture
## Modular Python AX.25 Node Software

`tncture` is a learning exercise for me as I experiment with packet radio. This is intended to be a replacement for software packages like LinBPQ. This software implements AX.25 Asynchronous Balanced Mode ("Connected Mode"), and then on top of that implements a "Node" software package appropriate for use on a AX.25 node. BUT! The intention is to keep these facilities separate so that it's possible to use tncture to dial a AX.25 node without running a node oneself, or write non-traditional node software packages that don't require connection through a traditional "Node" software.
