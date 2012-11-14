HTTP File Server
================

This project provides users a easy-to-use command-line file server that helps
you quickly share files using the http protocol. Receivers can just open the
browser and browse the directory and files you have shared. The package also
comes with two wrapper scripts: share and receive. These two scripts invokes
the file server and copy the link to the clipboard, making it easier to share
your files instantly. To use these two scripts, you must have xclip installed
on your system.

hfs is written in python and (in theory) runs on all platforms supported by
cpython 2.6 or later. However, it cannot read unicode filenames on Windows. The
two wrapper scripts are bash scripts and only run on unix-like systems.

hfs.py
---

### Usage

	hfs.py [-h] [-p PORT] [-f] [--enable-tar] [--rate-limit RATE_LIMIT]
				  [--upload-path UPLOAD_PATH] [--upload-rate-limit UPLOAD_RATE_LIMIT]
				  [file [file ...]]

`file` can be either a file or a directory.

### Optional Arguments

	  -h, --help            show this help message and exit
	  -p PORT, --port PORT  the port to listen on
	  -f, --follow-link     follow symbolic links when listing files; disabled by
									default
	  --enable-tar          enable remote user to download mutiple files at once
									in a tar archive
	  --rate-limit RATE_LIMIT
									single file download rate limit in KB/s
	  --upload-path UPLOAD_PATH
	  --upload-rate-limit UPLOAD_RATE_LIMIT
									single file upload rate limit in KB/s

hfs-share
-----

### Usage

	hfs-share <file1> [<file2> [ <file3> ...]]

`file` can be either a file or a directory.

hfs-receive
-------

### Usage

	hfs-receive <directory>

Received files will be saved to `directory`.

Usage Examples
--------------

Start the file server on port 8000 and share all jpg files in the directory.

	hfs -p 8000 *.jpg

Start the file server on port 8000 and share the root folder
(please *never* try this on your computer):

	hfs -p 8000 /

Start the file server, share hello.txt and copy the link to clipboard.

	hfs-share hello.txt

Start the file server, wait for uploads, and copy the link to clipboard. The uploads are saved to home folder (~).

	hfs-receive ~
