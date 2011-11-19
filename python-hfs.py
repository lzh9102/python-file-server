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

# the prefix to add before the root directory
# For example, if PREFIX is "/root" and the host is 127.0.0.1, then
# the root directory is http://127.0.0.1/root
PREFIX = "/"

if not PREFIX.startswith('/'):
    PREFIX = '/' + PREFIX
if PREFIX.endswith('/'):
    PREFIX = PREFIX[0:len(PREFIX)-1]

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

def prefix(path):
    """ Get the top-level folder in path.
        For example, the output for "/usr/bin/python" will be "/usr" """
    slash_index = path[1:].find('/')
    if slash_index >= 0:
        return path[0:slash_index+1]
    else:
        return path
    
def strip_prefix(path):
    """ Remove the top-level folder name in path
        For example, if path is "/usr/bin/python", the output will be "/bin/python"
        If the input is "/", then the output will be also "/".
        """
    result = path
    if result[0] == '/':
        result = result[1:]
    slash_index = result.find('/')
    
    if slash_index >= 0:
        result = result[slash_index:]
    else:
        result = ""
        
    if len(result) == 0: # '/' should be translated to '/'
        result = "/"
    return result

def strip_suffix(path):
    result = path
    if result.endswith("/"):
        result = result[0:len(result)-1]
    slash_index = result.rfind('/')
    if slash_index >= 0:
        result = result[0:slash_index]
    if len(result) == 0:
        result = '/'
    return result

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
    <style type="text/css">
    tr.trodd {
        background-color: #E6FFCC
    }
    tr.treven {
        background-color: #CCFFFF
    }
    </style>
    </head>
    <body>%(BODY)s</body>
</html>
"""
def generate_folder_listing_html(body):
    return FOLDER_LISTING_TEMPLATE % {"BODY": body}

REDIRECT_TEMPLATE = """
<html class="html">
    <head>
    <meta http-equiv="Refresh" content="0; url=%(ROOT)s" />
    </head>
    <body><a href="%(ROOT)s">%(ROOT)s</a></body>
</html>
"""
def generate_redirect_html(url):
    return REDIRECT_TEMPLATE % {"ROOT": url}

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
def generate_file_not_found_html(file):
    return FILE_NOT_FOUND_TEMPLATE % {"FILE": file}

# HTTP Reply
HTTP_OK = 200
HTTP_NOCONTENT = 204
HTTP_NOTFOUND = 404
HTTP_MOVED_PERMANENTLY = 301

class ThreadedHttpServer(ThreadingMixIn, BaseHTTPServer.HTTPServer):
    """ This class combines two classes ThreadingMixIn and BaseHTTPServer.HTTPServer
        to create a multi-threaded server that can handle multiple connections simultaneously. """
    pass

class MyServiceHandler(SimpleHTTPRequestHandler):
    """ This class provides HTTP service to the client """
    def do_GET(self):
        """ Handle http GET request from client. """
        path = urllib.unquote(self.path)
        host = "http://" + self.headers["host"]
        
        print("Request File: " + path)
        
        if len(PREFIX) == 0 or prefix(path) == PREFIX:
            """ Handle Virtual Filesystem """
            # strip path with PREFIX
            if len(PREFIX) != 0:
                path = strip_prefix(path)
            
            full_path = OPT_ROOT_DIR + path           
            
            if directory_exists(full_path):
                """ Handle directory listing. """
                self.send_response(HTTP_OK)
                self.send_header("Content-Type", "text/html;charset=UTF-8")
                self.end_headers()
                
                content = self.generate_folder_listing(host, OPT_ROOT_DIR, path)
                
                self.wfile.write(content)
                
            elif is_file(full_path):
                """ Handle file downloading. """
                self.send_file(full_path)
                
            else:
                """ Handle File Not Found error. """
                self.send_response(HTTP_OK)
                self.send_header("Content-Type", "text/html;charset=UTF-8")
                self.end_headers()
                
                content = generate_file_not_found_html(path)
                
                self.wfile.write(content)
        elif path == "/": # redirect '/' to /PREFIX
            self.send_response(HTTP_OK) # redirect
            self.send_header("Content-Type", "text/html;charset=UTF-8")
            self.wfile.write(generate_redirect_html(host + PREFIX))
            
        else: # data file
#            full_path = concat_folder_file(strip_suffix(argv[0]), "data") + path
#            print("Request Data File: " + full_path)
#            if is_file(full_path):
#                self.send_file(full_path)
            self.send_response(HTTP_NOTFOUND, "Not Found")
            
    def send_file(self, filename):
        """ Read the file and send it to the client. """
        self.send_response(HTTP_OK)
        type,encoding = mimetypes.guess_type(filename)
        filesize = os.path.getsize(filename)
        last_modified = self.date_time_string(int(os.path.getmtime(filename)))
        
        self.send_header("Content-Type", "%(TYPE)s;charset=%(ENCODING)s" % \
                         {"TYPE": type, "ENCODING": encoding})
        self.send_header("Content-Length", str(filesize))
        self.send_header("Last-Modified", last_modified)
        self.end_headers()
        
        with open(filename, "rb") as f:
            while 1:
                chunk = f.read(OPT_CHUNK_SIZE)
                if chunk:
                    self.wfile.write(chunk)
                else:
                    break
    
    def generate_parent_link(self, host, folder):
        """ Generate link for the parent directory of "folder" """
        if folder == "/":
            return "<u>Up</u>"

        parent_dir = folder
        if parent_dir.endswith('/'): # remove trailing '/'
            parent_dir = parent_dir[0:len(parent_dir)-2]
        
        last_slash_index = parent_dir.rfind('/')
        if last_slash_index >= 0:
            parent_dir = parent_dir[0:last_slash_index]
        
        return "<a href='" + host + PREFIX + parent_dir + "'>Up</a>"
            
    
    def generate_home_link(self, host):
        """ Generate link for root directory """
        return "<a href='" + host + PREFIX + "'>Home</a>"
            
    def generate_link(self, host, root, folder, file):
        """ Generate html link for a file/folder. """
        link = host + PREFIX + concat_folder_file(folder, file)
        return "<a href='%(LINK)s'>%(NAME)s</a> " % \
            {"LINK": link, "NAME": cgi.escape(file)}
            
    def generate_table_row(self, index, *fields):
        """ Generate a html table row with fields.
            The index is used to decide the color of the row.
            If index is negative, the color does not change. """
        # assign the class of odd-numbered rows to "trodd" and even-numbered rows to "treven"
        if index >= 0:
            result = ("<tr class='trodd'>" if index & 1 else "<tr class = 'treven'>")
        else: # don't change the class
            result = "<tr>"
        for f in fields:
            result += "<td>" + str(f) + "</td>"
        result += "</tr>"
        return result
                
    def list_files(self, host, root, folder):
        """ List all the files in html. """
        i = 1 # this index is used to decide the color of a row
        body = ""
        path = root + folder
        fileList = sorted(os.listdir(path))
        
        body += "<table>"
        
        # table title
        body += self.generate_table_row(-1, "File", "Size", "Last Modified")
        body += self.generate_table_row(-1, "", "", "")
        
        # list subfolders
        for f in fileList:
            if not is_file(concat_folder_file(path, f)): # is directory
                body += self.generate_table_row(i, "(DIR) " + \
                    self.generate_link(host, root, folder, f) \
                    , "", "")
                i += 1
        
        # list files
        for f in fileList:
            full_filename = concat_folder_file(path, f)
            if is_file(full_filename): # is file
                last_modified = self.date_time_string(os.path.getmtime(full_filename))
                body += self.generate_table_row(i, \
                    self.generate_link(host, root, folder, f) \
                    , human_readable_size(os.path.getsize(full_filename))
                    , last_modified)
                i += 1
                
        body += "</table>"
                              
        return body

    def generate_folder_listing(self, host, root, folder):
        """ Generate the file listing HTML for a folder. """
        path = root + folder
        
        body = self.generate_parent_link(host, folder) + "&nbsp;&nbsp;&nbsp" + \
               self.generate_home_link(host) + "<br>" + \
               folder + "<hr>"
        
        if directory_exists(path):
            body += self.list_files(host, root, folder)
            
        body += "<hr>"

        return generate_folder_listing_html(body)
    
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
if len(OPT_ROOT_DIR) == 0:
    OPT_ROOT_DIR = "/"
    print("Warning: You have shared the entire filesystem.")

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
    server.daemon_threads = True
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
