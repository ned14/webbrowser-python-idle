# Welcome to Python-in-Linux-in-browser!

If you are reading this, it is from within a _real_ copy of Linux running
in your web browser by Just-In-Time compiled WebAssembly from i386 binaries.
Or, put another way, a fake computer running a real operating system within
your web browser -- and which can run seamlessly on locked down computers
which only run a web browser and nothing else such as Chromebooks.

This particular copy of Linux does exactly a few things:

1. It launches into a file manager, which you've already seen if you've
reached here.
2. That file manager can launch a viewer of most common text and image
files.
3. It can also open Python source files in Python IDLE, the Python
integrated development environment.
4. As that is a real Python running in a real Linux, a full and complete
`tkinter` module is available, unlike for most Python-within-your-browser
implementations which cannot implement `tkinter` within a web browser.
5. Having a fully working `tkinter` and Python IDLE means that most of
the beginner programming text books work exactly as advertised without
any workarounds needed. You get full fat Python within your web browser!

If this excites you, its github project can be found at:

https://github.com/ned14/webbrowser-python-idle

If you run this project in your own environment (it is a standard
`docker compose`), you can configure storage backends which mean
that files within this Linux are synced back to the Docker image.
That means files you edit and save here persist over time! No
matter which computer or web browser you use.

Enjoy!
