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

# test
import time


SERV_PORT = 8001
ROOT_DIR = "/home/timothy/"

# the number of bytes to read in a chunk when reading a file
CHUNK_SIZE = 1024

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
    pass

class MyServiceHandler(SimpleHTTPRequestHandler):

    def do_GET(self):
        """ Handle http GET request from client. """
        path = urllib.unquote(self.path)
        full_path = ROOT_DIR + path
        host = "http://" + self.headers["host"]
        
        print("Request File: " + full_path)
        
        if directory_exists(full_path):
            """ Handle directory listing. """
            self.send_response(HTTP_OK)
            self.send_header("Content-Type", "text/html;charset=UTF-8")
            self.end_headers()
            
            content = self.generate_folder_listing(host, ROOT_DIR, path)
            
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
                    chunk = f.read(CHUNK_SIZE)
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
        
        return "<a href='" + concat_folder_file(host, parent_dir) + "'>Up</a>"
    
    def generate_home_link(self, host):
        """ Generate link for root directory """
        return "<a href='" + host + "'>Home</a>"
            
    def generate_link(self, host, root, folder, file):
        """ Generate html link for a file/folder. """
        path = root + concat_folder_file(folder, file)
        if not os.path.exists(path):
            return ""
        else:
            link = host + concat_folder_file(folder, file)
            
            return "<a href='%(LINK)s'>%(NAME)s</a>" % \
                {"LINK": link, "NAME": cgi.escape(file)}

    def generate_folder_listing(self, host, root, folder):
        """ Generate the file listing HTML for a folder. """
        path = root + folder
        
        body = self.generate_parent_link(host, folder) + "&nbsp;&nbsp;&nbsp" + \
               self.generate_home_link(host) + "<br>" + \
               folder + "<hr>"
        if directory_exists(path):
            fileList = os.listdir(path)
            for f in fileList: # list every file in the folder
                if not is_file(concat_folder_file(path, f)): # is directory
                    body += "(DIR) " # add (DIR) prefix to notify the user
                body += self.generate_link(host, root, folder, f)
                body += "<br>"

        return FOLDER_LISTING_TEMPLATE % {"BODY": body}

if not directory_exists(ROOT_DIR):
    sys.stderr.write("Error: Root directory does not exist.\n")
    exit(-1)
        
server = ThreadedHttpServer(('', SERV_PORT), MyServiceHandler)
server.serve_forever()
