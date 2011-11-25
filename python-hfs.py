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
import time
import posixpath
import locale
import argparse

###### Options and default values ######

# the port to listen on
OPT_PORT = 80

# the root directory of the virtual filesystem
OPT_ROOT_DIR = ""

# the number of bytes to read in a chunk when reading a file
OPT_CHUNK_SIZE = 1024

# whether to follow symlink folders
OPT_FOLLOW_LINK = False

# file transmission rate limit in bytes
OPT_RATE_LIMIT = 1024 * 1024 * 10

# the prefix to add before the root directory
# For example, if PREFIX is "/root" and the host is 127.0.0.1, then
# the root directory is http://127.0.0.1/root
PREFIX = "/"

###### Helper Functions ######

def is_file(path):
    return os.path.isfile(path)

def is_dir(path, AllowLink=False):
    """ Determine whether path is a directory, excluding symbolic links """
    return os.path.isdir(path) and (AllowLink or (not os.path.islink(path)))

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

def WRITE_LOG(message, client=None):
    t = time.localtime()
    timestr = "%4d-%02d-%02d %02d:%02d:%02d" % \
        (t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec)
    
    output = "[" + timestr + "] "
    if client != None:
        output += "Client " + client + ": "
    output += message
    
    print(output)

def DEBUG(message):
    sys.stderr.write("DEBUG: %s\n" % (message))
    
class RateLimiter:
    MAX_PRECISION = 0.1

    def __init__(self, maxrate):
        """ @param rate allowed calls to limit() per second; a value of 0
                    means no limit.  """
        if maxrate == 0:
            self.limit = lambda: 0
        else:
            self.__period = 1.0 / maxrate
            self.__prev_time = time.time()
            self.__counter = 0
            self.__counter_max = 0
            self.limit = lambda: self.__call_limit()
    
    def __call_limit(self):
        self.__counter += 1
        if self.__counter > self.__counter_max:
            self.__counter = 0
            interval = time.time() - self.__prev_time
            min_interval = self.__period * (self.__counter_max + 1)
            
            if interval < min_interval:
                time.sleep(min_interval - interval)

            if interval < self.MAX_PRECISION:
                self.__counter_max += 1
            elif interval > 2 * self.MAX_PRECISION and self.__counter_max > 0:
                self.__counter_max -= 1

            self.__prev_time = time.time()

__system_encoding = locale.getdefaultlocale()[1]
def get_system_encoding():
    return __system_encoding
    
###### HTML Templates ######

FOLDER_LISTING_TEMPLATE = """
<html class="html">
    <head>
    <title>Python HTTP File Server</title>
    <style type="text/css">
    tr.tr_odd {
        background-color: #E6FFCC
    }
    tr.tr_even {
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
        
        DEBUG("HTTP GET Request: " + path)
        
        if len(PREFIX) == 0 or prefix(path) == PREFIX:
            """ Handle Virtual Filesystem """
            # strip path with PREFIX
            if len(PREFIX) != 0:
                path = strip_prefix(path)
            
            full_path = self.get_local_path(path=path, rootdir=OPT_ROOT_DIR)
            DEBUG("full_path: " + full_path)
            
            if is_dir(full_path, AllowLink=OPT_FOLLOW_LINK):
                """ Handle directory listing. """
                DEBUG("List Dir: " + full_path)
                self.send_response(HTTP_OK)
                self.send_header("Content-Type", "text/html;charset=%(ENCODING)s"
                                 % {"ENCODING": get_system_encoding()})
                self.end_headers()
                
                content = self.generate_folder_listing(OPT_ROOT_DIR, path)
                
                self.wfile.write(content)
                
            elif is_file(full_path):
                """ Handle file downloading. """
                DEBUG("Download File: " + full_path)
                client = self.address_string()
                
                try:                    
                    WRITE_LOG("Start Downloading %s" % (path), client)
                    
                    t0 = time.time()
                    size = self.send_file(full_path, RateLimit=OPT_RATE_LIMIT)
                    seconds = time.time() - t0
                    
                    if seconds > 1:
                        download_rate = "(%s/sec)" % (hrs(float(size)/seconds))
                    else:
                        download_rate = ""
                    
                    hrs = human_readable_size; # abbreviate the function
                    WRITE_LOG("Fully Downloaded %s - %s @ %d sec %s"
                        % (path, hrs(size), seconds, download_rate), client)
                except Exception:
                    WRITE_LOG("Downloading Failed: %s" % (path), client)
                    DEBUG("Downloading Failed: " + full_path)
                
            else:
                """ Handle File Not Found error. """
                self.send_response(HTTP_OK)
                self.send_header("Content-Type", "text/html;charset=%(ENCODING)s"
                                 % {"ENCODING": get_system_encoding()})
                self.end_headers()
                
                content = generate_file_not_found_html(path)
                
                self.wfile.write(content)
        elif path == "/": # redirect '/' to /PREFIX
            self.send_response(HTTP_OK) # redirect
            self.send_header("Content-Type", "text/html;charset=%(ENCODING)s"
                             % {"ENCODING": get_system_encoding()})
            self.wfile.write(generate_redirect_html(PREFIX))
            
        else: # data file
#            full_path = concat_folder_file(strip_suffix(argv[0]), "data") + path
#            print("Request Data File: " + full_path)
#            if is_file(full_path):
#                self.send_file(full_path)
            self.send_response(HTTP_NOTFOUND, "Not Found")
            
    def get_local_path(self, path, rootdir=None):
        """ Translate a filename separated by "/" to the local file path. """
        path = posixpath.normpath(path)
        wordList = path.split('/')
        
        if rootdir == None:
            path = ""
        else:
            path = rootdir
            
        for word in wordList:
            drive, word = os.path.splitdrive(word)
            head, word = os.path.split(word)
            if word in (os.curdir, os.pardir): continue
            path = os.path.join(path, word)
        
        return path
            
    def send_file(self, filename, RateLimit=0):
        """ Read the file and send it to the client.
            If the function succeeds, it returns the file size in bytes. """
        self.send_response(HTTP_OK)
        type,encoding = mimetypes.guess_type(filename)
        filesize = os.path.getsize(filename)
        last_modified = self.date_time_string(int(os.path.getmtime(filename)))
        
        self.send_header("Content-Type", "%(TYPE)s;charset=%(ENCODING)s" % \
                         {"TYPE": type, "ENCODING": encoding})
        self.send_header("Content-Length", str(filesize))
        self.send_header("Last-Modified", last_modified)
        self.end_headers()
        
        if RateLimit == 0:
            limiter = RateLimiter(0) # no limit
        else:
            limiter = RateLimiter(float(RateLimit) / OPT_CHUNK_SIZE)
        
        with open(filename, "rb") as f:
            while 1:
                chunk = f.read(OPT_CHUNK_SIZE)
                if chunk:
                    self.wfile.write(chunk)
                else:
                    break
                limiter.limit()
        
        return filesize
    
    def generate_parent_link(self, folder):
        """ Generate link for the parent directory of "folder" """
        if folder == "/":
            return "<u>Up</u>"

        parent_dir = strip_suffix(folder)
        
        return "<a href='" + urllib.quote(PREFIX + parent_dir) + "'>Up</a>"
            
    
    def generate_home_link(self):
        """ Generate link for root directory """
        return "<a href='" + PREFIX + "'>Home</a>"
            
    def generate_link(self, root, folder, file, text=None):
        """ Generate html link for a file/folder. """
        text = (file if text == None else text)
        link = PREFIX + concat_folder_file(folder, file)
        link = (link[0:len(link)-1] if link.endswith('/') else link) # strip trailing '/'
        return "<a href='%(LINK)s'>%(NAME)s</a> " % \
            {"LINK": urllib.quote(link), "NAME": cgi.escape(text)}
            
    def generate_table_row(self, index, *fields):
        """ Generate a html table row with fields.
            The index is used to decide the color of the row.
            If index is negative, the color does not change. """
        # assign the class of odd-numbered rows to "tr_odd" and even-numbered rows to "tr_even"
        if index >= 0:
            result = ("<tr class='tr_odd'>" if index & 1 else "<tr class = 'tr_even'>")
        else: # don't change the class
            result = "<tr>"
        for f in fields:
            result += "<td>" + str(f) + "</td>"
        result += "</tr>"
        return result
                
    def list_files(self, root, folder):
        """ List all the files in html. """
        i = 1 # this index is used to decide the color of a row
        body = ""
        path = root + folder
        fileList = sorted(os.listdir(path))
        
        body += "<table>"
        
        # table title
        body += self.generate_table_row(-1, "File", "Size", "Last Modified")
        body += self.generate_table_row(-1, "", "", "")
        
        # generate "." directory
        body += self.generate_table_row(i, \
            "(DIR) " + self.generate_link(root, folder, "", ".") \
            , "", "")
        i += 1
        
        # list subfolders
        for f in fileList:
            if is_dir(concat_folder_file(path, f), AllowLink=OPT_FOLLOW_LINK):
                body += self.generate_table_row(i, "(DIR) " + \
                    self.generate_link(root, folder, f) \
                    , "", "")
                i += 1
        
        # list files
        for f in fileList:
            full_filename = concat_folder_file(path, f)
            if is_file(full_filename): # is file
                last_modified = self.date_time_string(os.path.getmtime(full_filename))
                body += self.generate_table_row(i, \
                    self.generate_link(root, folder, f) \
                    , human_readable_size(os.path.getsize(full_filename))
                    , last_modified)
                i += 1
                
        body += "</table>"
                              
        return body

    def generate_folder_listing(self, root, folder):
        """ Generate the file listing HTML for a folder. """
        path = root + folder
        
        body = self.generate_parent_link(folder) + "&nbsp;&nbsp;&nbsp" + \
               self.generate_home_link() + "<br>" + \
               folder + "<hr>"
        
        if is_dir(path, AllowLink=OPT_FOLLOW_LINK):
            body += self.list_files(root, folder)
            
        body += "<hr>"

        return generate_folder_listing_html(body)
    
    def get_param(self, key):
        if key in self.__params:
            return self.__params[key]
        else:
            return ""
    
    def parse_params(self):
        """ Parse the parameters from url and request body """

        # init the params
        self.__params = {}

        # get the params in query string
        if self.path.find('?') != -1:
            self.path, qs = self.path.split("?", 1)

            for pair in qs.split("&"):
                key, value = pair.split("=")
                self.__params[key] = value

def parse_command_line():
    """ Parse command line option subroutine """
    global OPT_ROOT_DIR, OPT_PORT, PREFIX
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
    
    if not is_dir(OPT_ROOT_DIR, AllowLink=True):
        print("Error: Root directory does not exist.\n")
        exit(-1)
        
    try:
        if argc >= 3:
            OPT_PORT = int(argv[2])
    except ValueError:
        print("Error: Port must be a positive integer value")
        exit(-1)
        
    if not PREFIX.startswith('/'):
        PREFIX = '/' + PREFIX
    if PREFIX.endswith('/'):
        PREFIX = PREFIX[0:len(PREFIX)-1]
    
###### Server Main Function ######
def server_main():
    """ The server main function """
    try:
        server = ThreadedHttpServer(('', OPT_PORT), MyServiceHandler)
        server.daemon_threads = True
        
        WRITE_LOG("Server Started")
        DEBUG("System Language: " + locale.getdefaultlocale()[0])
        DEBUG("System Encoding: " + locale.getdefaultlocale()[1])
        
        server.serve_forever()
    except socket.error, e:
        if e.errno == 13: # permission denied
            sys.stderr.write("Error: Permission Denied.\n")
            if OPT_PORT < 1024:
                sys.stderr.write("(Notice: Unix requires root privilege to bind on port<1024)\n")
        elif e.errno == 98: # address already in use
            sys.stderr.write("Error: Address already in use. Please try again later.\n")
        else:
            DEBUG(e)
    except KeyboardInterrupt:
        sys.stderr.write("Server Terminated\n")
    pass

if __name__ == "__main__":
    parse_command_line()
    server_main()

