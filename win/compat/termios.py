"""Windows stub for the POSIX-only `termios` module.

google-colab-cli (0.7.x) imports termios/tty at module load via colab_cli.console, so
EVERY `colab` command crashes on native Windows with ModuleNotFoundError. Only
`colab console` actually uses it. win/common.ps1 puts this directory on PYTHONPATH so
upload/exec/stop/sessions work; `colab console` itself will not (use `colab exec`).
"""
error = OSError
TCSANOW, TCSADRAIN, TCSAFLUSH = 0, 1, 2
# indices / flags referenced by the stdlib `tty` module
IFLAG, OFLAG, CFLAG, LFLAG, ISPEED, OSPEED, CC = range(7)
BRKINT = ICRNL = INPCK = ISTRIP = IXON = OPOST = CSIZE = PARENB = CS8 = 0
ECHO = ICANON = IEXTEN = ISIG = ECHOE = ECHOK = ECHONL = ICRNL = INLCR = IGNCR = 0
VMIN, VTIME = 6, 5


def _unsupported(*_a, **_k):
    raise OSError("termios is not available on Windows (colab console is unsupported here)")


tcgetattr = tcsetattr = tcsendbreak = tcdrain = tcflush = tcflow = _unsupported
