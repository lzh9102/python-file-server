#!/usr/bin/env python
# -*- coding: utf-8 -*-

import BaseHTTPServer
from SimpleHTTPServer import SimpleHTTPRequestHandler
from SocketServer import ThreadingMixIn
import os
import sys
import stat
import cgi
import urllib
import threading
import mimetypes
import socket

###### Options and default values ######

# the port to listen on
OPT_PORT = 80

# the root directory of the virtual filesystem
OPT_ROOT_DIR = ""

# the number of bytes to read in a chunk when reading a file
OPT_CHUNK_SIZE = 1024

###### Helper Functions ######

def directory_exists(path):
    """ Determine whether a folder exists. """
    if not os.path.exists(path):
        return False
    mode = os.stat(path).st_mode
    return stat.S_ISDIR(mode)

def is_file(path):
    """ Determine whether variable "path" is a file (i.e. not a folder). """
    if not os.path.exists(path):
        return False
    mode = os.stat(path).st_mode
    return not stat.S_ISDIR(mode)

def concat_folder_file(folder, file):
    """ Concatenate folder name with file name.
        If the folder name ends with a '/', the character will be removed """
    if folder.endswith("/") or folder == "/" or folder == "":
        return folder + file
    else:
        return folder + "/" + file

def human_readable_size(nsize):
    K = 1024
    M = K * 1024
    G = M * 1024
    if nsize > G:
        return "%(SIZE).1f GiB" % {"SIZE": float(nsize) / G}
    if nsize > M:
        return "%(SIZE).1f MiB" % {"SIZE": float(nsize) / M}
    if nsize > K:
        return "%(SIZE).1f KiB" % {"SIZE": float(nsize) / K}
    return str(nsize) + " B"
    
###### HTML Templates ######

FOLDER_LISTING_TEMPLATE = """
<html class="html">
    <head>
    <title>Python HTTP File Server</title>
    </head>
    <body>%(BODY)s</body>
</html>
"""

FILE_NOT_FOUND_TEMPLATE = """
<html class="html">
    <head>
    <title>%(FILE)s: File Not Found</title>
    </head>
    <body style='font-size: 50'>
    <font color=red>%(FILE)s doesn't exist on the server.</font>
    </body>
</html>
"""

# HTTP Reply
HTTP_OK = 200
HTTP_NOCONTENT = 204
HTTP_NOTFOUND = 404

class ThreadedHttpServer(ThreadingMixIn, BaseHTTPServer.HTTPServer):
    """ This class combines two classes ThreadingMixIn and BaseHTTPServer.HTTPServer
        to create a multi-threaded server that can handle multiple connections simultaneously. """
    pass

class MyServiceHandler(SimpleHTTPRequestHandler):
    """ This class provides HTTP service to the client """
    def do_GET(self):
        """ Handle http GET request from client. """
        path = urllib.unquote(self.path)
        full_path = OPT_ROOT_DIR + path
        host = "http://" + self.headers["host"]
        
        print("Request File: " + full_path)
        
        if directory_exists(full_path):
            """ Handle directory listing. """
            self.send_response(HTTP_OK)
            self.send_header("Content-Type", "text/html;charset=UTF-8")
            self.end_headers()
            
            content = self.generate_folder_listing(host, OPT_ROOT_DIR, path)
            
            self.wfile.write(content)
            
        elif is_file(full_path):
            """ Handle file downloading. """        
            self.send_response(HTTP_OK)
            
            type,encoding = mimetypes.guess_type(full_path)
            self.send_header("Content-Type", "%(TYPE)s;charset=%(ENCODING)s" % \
                             {"TYPE": type, "ENCODING": encoding})
            self.end_headers()
            
            # Read the file and send it to the client
            with open(full_path, "rb") as f:
                while 1:
                    chunk = f.read(OPT_CHUNK_SIZE)
                    if chunk:
                        self.wfile.write(chunk)
                    else:
                        break
            
        else:
            """ Handle File Not Found error. """
            self.send_response(HTTP_OK)
            self.send_header("Content-Type", "text/html;charset=UTF-8")
            self.end_headers()
            
            content = FILE_NOT_FOUND_TEMPLATE % {"FILE": path}
            
            self.wfile.write(content)
            
    def generate_parent_link(self, host, folder):
        """ Generate link for the parent directory of "folder" """
        parent_dir = folder
        if parent_dir.endswith('/'): # remove trailing '/'
            parent_dir = parent_dir[0:len(parent_dir)-2]
        
        last_slash_index = parent_dir.rfind('/')
        if last_slash_index >= 0:
            parent_dir = parent_dir[0:last_slash_index]
            
        if len(parent_dir) > 0 and parent_dir[0] == '/': # remove leading '/'
            parent_dir = parent_dir[1:]
        
        if folder == "/": # already at root directory
            return "<u>Up</u>"
        else:
            return "<a href='" + concat_folder_file(host, parent_dir) + "'>Up</a>"
            
    
    def generate_home_link(self, host):
        """ Generate link for root directory """
        return "<a href='" + host + "'>Home</a>"
            
    def generate_link(self, host, root, folder, file):
        """ Generate html link for a file/folder. """
        link = host + concat_folder_file(folder, file)
        return "<a href='%(LINK)s'>%(NAME)s</a> " % \
            {"LINK": link, "NAME": cgi.escape(file)}
                
    def list_files(self, host, root, folder):
        body = ""
        path = root + folder
        fileList = os.listdir(path)
        for f in fileList: # list every file in the folder
            if is_file(concat_folder_file(path, f)): # is directory
                body += self.generate_link(host, root, folder, f)
                body += '(' + human_readable_size(os.path.getsize(path)) + ')'
                body += "<br>"
        return body
    
    def list_subfolders(self, host, root, folder):
        body = ""
        path = root + folder
        fileList = os.listdir(path)
        for f in fileList: # list every file in the folder
            if not is_file(concat_folder_file(path, f)): # is directory
                body += "(DIR) " # add (DIR) prefix to notify the user
                body += self.generate_link(host, root, folder, f)
                body += "<br>"
        return body

    def generate_folder_listing(self, host, root, folder):
        """ Generate the file listing HTML for a folder. """
        path = root + folder
        
        body = self.generate_parent_link(host, folder) + "&nbsp;&nbsp;&nbsp" + \
               self.generate_home_link(host) + "<br>" + \
               folder + "<hr>"
        
        if directory_exists(path):
            body += self.list_subfolders(host, root, folder)
            body += self.list_files(host, root, folder)

        return FOLDER_LISTING_TEMPLATE % {"BODY": body}
    
###### Main Function ######

# Read options from commandline.
argv = sys.argv
argc = len(argv)

if argc != 2 and argc != 3:
    print("Usage: " + argv[0] + " <root_dir> [port]")
    exit(-1)

OPT_ROOT_DIR = argv[1]
if OPT_ROOT_DIR.endswith('/'):
    OPT_ROOT_DIR = OPT_ROOT_DIR[0:len(OPT_ROOT_DIR)-1] # strip trailing '/'

if not directory_exists(OPT_ROOT_DIR):
    print("Error: Root directory does not exist.\n")
    exit(-1)
    
try:
    if argc >= 3:
        OPT_PORT = int(argv[2])
except ValueError:
    print("Error: Port must be a positive integer value")
    exit(-1)
    
try:
    server = ThreadedHttpServer(('', OPT_PORT), MyServiceHandler)
    server.serve_forever()
except socket.error, e:
    if e.errno == 13: # permission denied
        sys.stderr.write("Error: Permission Denied.\n")
        if OPT_PORT < 1024:
            sys.stderr.write("(Notice: Unix requires root privilege to bind on port<1024)\n")
    elif e.errno == 98: # address already in use
        sys.stderr.write("Error: Address already in use. Please try again later.\n")
    else:
        print(e)
except KeyboardInterrupt:
    sys.stderr.write("Server Terminated\n")