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
import tarfile
import uuid

###### Options and default values ######

# the port to listen on
OPT_PORT = 80

# the number of bytes to read in a chunk when reading a file
OPT_CHUNK_SIZE = 1024

# whether to follow symlink folders
OPT_FOLLOW_LINK = False

# file transmission rate limit in bytes
OPT_RATE_LIMIT = 1024 * 1024 * 10

# whether to allow downloading as archive
OPT_ALLOW_DOWNLOAD_TAR = False

# the prefix to add before the root directory
# For example, if PREFIX is "/root" and the host is 127.0.0.1, then
# the root directory is http://127.0.0.1/root
PREFIX = "/files"
DOWNLOAD_TAR_PREFIX = "/download_tar"

# The list of files appearing in the root of the virtual filesystem.
# TODO: Implement locking to protect concurrent access.
SHARED_FILES = {}
def add_shared_file(key, path):
    final_key = key
    index = 2
    while SHARED_FILES.has_key(final_key): # Append an index if the filename alreaady exists.
        final_key = "%s (%d)" % (key, index)
        index += 1
    SHARED_FILES[final_key] = path
    
def get_shared_file(key):
    if key not in SHARED_FILES:
        return ""
    else:
        return SHARED_FILES[key]

def remove_shared_file(key):
    try:
        SHARED_FILES.pop(key)
    except Exception:
        pass

def get_shared_files():
    return SHARED_FILES.keys()

DOWNLOAD_UUID = {} # map from uuid to filelist
DOWNLOAD_UUID_LOCK = threading.Lock()
def push_download(fileList, uuid):
    with DOWNLOAD_UUID_LOCK:
        DOWNLOAD_UUID[uuid] = fileList
    
def pop_download(uuid):
    with DOWNLOAD_UUID_LOCK:
        if uuid in DOWNLOAD_UUID: # return and remove the download request
            fileList = DOWNLOAD_UUID[uuid]
            DOWNLOAD_UUID.pop(uuid)
            return fileList
        else:
            return []

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

def suffix(path):
    return os.path.basename(path)
    
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
            
class RateLimitingWriter:
    """ Limit the writing rate to the file """
    def __init__(self, file, maxrate):
        """ Constructor of RateLimitingWriter
            @param file the file object to be written to.
            It can be any object with write() method.
            @param maxrate maximum bytes to write per second """
        self.__file = file
        self.__limiter = RateLimiter(maxrate)

    def write(self, data):
        length = len(data)
        nleft = length
        index = 0
        while nleft > 0:
            end = index + OPT_CHUNK_SIZE
            end = (length if end > length else end)
            self.__file.write(data[index:end])
            nleft -= OPT_CHUNK_SIZE
            index += OPT_CHUNK_SIZE
            self.__limiter.limit()

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
    <meta http-equiv="Refresh" content="0; url=%(TARGET)s" />
    </head>
    <body>%(BODY)s</body>
</html>
"""
def generate_redirect_html(url, body=None):
    if body == None:
        body = "redirect: <a href='%(TARGET)s'>%(TARGET)s</a>" % {"TARGET": url}
    return REDIRECT_TEMPLATE % {"TARGET": url, "BODY": body}

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
    def log_message(self, format, *args):
        DEBUG("HTTP Server: " + (format % args))
        
    def do_GET(self):
        """ Handle http GET request from client. """
        path = urllib.unquote(self.path)
        
        DEBUG("HTTP GET Request: " + path)
        
        self.parse_params()
        path = path.split("?")[0] # strip arguments from path
        
        if len(PREFIX) == 0 or prefix(path) == PREFIX:
            """ Handle Virtual Filesystem """
            # strip path with PREFIX
            if len(PREFIX) != 0:
                path = strip_prefix(path)
            
            localpath = self.get_local_path(path)
            DEBUG("localpath: " + localpath)
            
            allow_link = (OPT_FOLLOW_LINK or strip_suffix(path) == "/")
            if path == "/" or is_dir(localpath, AllowLink=allow_link):
                """ Handle directory listing. """
                DEBUG("List Dir: " + localpath)
                is_download_mode = OPT_ALLOW_DOWNLOAD_TAR and (self.get_param("dlmode") == "1")
                content = self.generate_folder_listing(path, localpath, is_download_mode)
                self.send_html(content)
                
            elif is_file(localpath):
                """ Handle file downloading. """
                DEBUG("Download File: " + localpath)
                client = self.address_string()
                
                try:                    
                    WRITE_LOG("Start Downloading %s" % (path), client)
                    
                    t0 = time.time()
                    size = self.send_file(localpath, RateLimit=OPT_RATE_LIMIT)
                    seconds = time.time() - t0
                    
                    hrs = human_readable_size; # abbreviate the function
                    if seconds > 1:
                        download_rate = "(%s/sec)" % (hrs(float(size)/seconds))
                    else:
                        download_rate = ""
                    
                    WRITE_LOG("Fully Downloaded %s - %s @ %d sec %s"
                        % (path, hrs(size), seconds, download_rate), client)
                except Exception, e:
                    WRITE_LOG("Downloading Failed: %s" % (path), client)
                    DEBUG("Downloading Failed: " + localpath + " (" + e.message + ")")
                
            else:
                """ Handle File Not Found error. """
                self.send_html(generate_file_not_found_html(path))
                
        elif path == "/": # redirect '/' to /PREFIX
            self.send_html(generate_redirect_html(PREFIX))
        elif OPT_ALLOW_DOWNLOAD_TAR and path == DOWNLOAD_TAR_PREFIX:
            self.send_tar_download(self.get_param("id"))
        else: # data file
            self.send_response(HTTP_NOTFOUND, "Not Found")
            
    def do_POST(self):
        self.parse_params()
        
        if OPT_ALLOW_DOWNLOAD_TAR and self.path == DOWNLOAD_TAR_PREFIX:
            clength = int(self.headers.dict['content-length'])
            content = urllib.unquote_plus(self.rfile.read(clength))
            virtualpath = self.get_param("r")
            fileList = []
            
            for pair in content.split("&"):
                try:
                    key, value = pair.split("=")
                except Exception:
                    key, value = (pair, "")
                if key == "chkfiles[]":
                    fileList.append(value)
                    
            if virtualpath != None:
                redirect_html_body = """
                <a href='%(DIR)s'>Back</a>
                <script language='javascript'>
                //<!--
                    document.write("<label id='txtTime'></label>")
                    function countdown(sec) {
                        if (sec > 0) {
                            txtTime.innerHTML = "(" + sec + ")"
                            setTimeout("countdown("+(sec-1).toString()+")", 1000);
                        } else { // timeup
                            window.location="%(DIR)s"
                        }
                    }
                    countdown(3)
                //-->
                </script>
                """ % {"DIR": virtualpath}
            else:
                virtualpath = ""
                redirect_html_body = "File Download" 
        
            if len(fileList) != 0:
                retrieve_code = str(uuid.uuid4())
                push_download(fileList, retrieve_code)
                self.send_html(
                    generate_redirect_html(DOWNLOAD_TAR_PREFIX + "?id=" + retrieve_code
                                           , body=redirect_html_body))
            else:
                self.send_html(generate_redirect_html(virtualpath))
            
    def get_local_path(self, path):
        """ Translate a filename separated by "/" to the local file path. """
        path = posixpath.normpath(path)
        wordList = path.split('/')
        wordList = wordList[1:] # remove the first item because it is always empty
        
        if len(wordList) == 0:
            return ""
        
        root = get_shared_file(wordList[0])
        if root == "":
            return ""
        wordList = wordList[1:]

        path = root
            
        for word in wordList:
            drive, word = os.path.splitdrive(word)
            head, word = os.path.split(word)
            if word in (os.curdir, os.pardir): continue
            path = os.path.join(path, word)
        
        return path
    
    def send_html(self, content):
        self.send_response(HTTP_OK) # redirect
        self.send_header("Content-Type", "text/html;charset=%(ENCODING)s"
                    % {"ENCODING": get_system_encoding()})
        self.send_no_cache_header()
        self.end_headers()
        self.wfile.write(content)
            
    def send_file(self, filename, RateLimit=0, AllowCache=False):
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
        if not AllowCache:
            self.send_no_cache_header()
        self.end_headers()
        
        if RateLimit == 0:
            rate_limit = 0 # no limit
        else:
            rate_limit = float(RateLimit) / OPT_CHUNK_SIZE
            
        writer = RateLimitingWriter(self.wfile, rate_limit)
        
        with open(filename, "rb") as f:
            while 1:
                chunk = f.read(OPT_CHUNK_SIZE)
                if chunk:
                    writer.write(chunk)
                else:
                    break
        
        return filesize
    
    def send_tar(self, virtualpaths, ArchiveName=None, RateLimit=0):
        if ArchiveName == None:
            ArchiveName = "archive.tar.gz"
        
        self.send_response(HTTP_OK)
        
        self.send_header("Content-Type", "application/x-tar")
        self.send_header("Content-Disposition", "attachment;filename=\"%s\""
                         % (ArchiveName))
        self.send_no_cache_header()
        self.end_headers()
        
        if RateLimit == 0:
            rate_limit = 0 # no limit
        else:
            rate_limit = float(RateLimit) / OPT_CHUNK_SIZE
        
        writer = RateLimitingWriter(self.wfile, rate_limit)
        
        with tarfile.open(fileobj=writer, mode="w|gz") as tar:
            for f in virtualpaths:
                localpath = self.get_local_path(f)
                name = suffix(f)
                DEBUG("send_tar: add file " + localpath + " as " + name)
                tar.add(localpath, name)
                
    def send_tar_download(self, id, ArchiveName=None):
        if id == None:
            return
        if ArchiveName == None:
            ArchiveName = "archive.tar.gz"

        fileList = pop_download(id)
        
        if len(fileList) != 0:
            self.send_tar(fileList, ArchiveName, OPT_RATE_LIMIT)
        else:
            self.send_html(generate_file_not_found_html(str("download " + id)))
                
    def send_no_cache_header(self):
        """ Send HTTP header to prevent browser caching. """
        self.send_header("Cache-Control", "no-cache, must-revalidate")
        self.send_header("Expires", "Sat, 26 Jul 1997 05:00:00 GMT") # date in the past
    
    def generate_parent_link(self, folder):
        """ Generate link for the parent directory of "folder" """
        if folder == "/":
            return "<u>Up</u>"

        parent_dir = strip_suffix(folder)
        
        return "<a href='" + urllib.quote(PREFIX + parent_dir) + "'>Up</a>"
            
    
    def generate_home_link(self):
        """ Generate link for root directory """
        link = ("/" if len(PREFIX) == 0 else PREFIX)
        return "<a href='" + link + "'>Home</a>"
    
    def generate_dlmode_link(self, virtualpath):
        link = PREFIX + virtualpath + "?dlmode=1"
        return "<a href='" + link + "'>Download Multiple Files</a>"
            
    def generate_link(self, virtualpath, text=None):
        """ Generate html link for a file/folder. """
        text = (suffix(virtualpath) if text == None else text)
        link = PREFIX + virtualpath
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
                
    def list_files(self, virtualpath, localpath, ShowCheckbox=False):
        """ List all the files in html. """
        i = 1 # this index is used to decide the color of a row
        body = ""

        if virtualpath == "/":
            fileList = sorted(get_shared_files()) # list virtual filesystem root
            is_root = True
        else:
            fileList = sorted(os.listdir(localpath))
            is_root = False
        
        body += "<table>"
        
        # table title
        body += self.generate_table_row(-1, "File", "Size", "Last Modified")
        body += self.generate_table_row(-1, "", "", "")
        
        # list subfolders
        for f in fileList:
            if ShowCheckbox:
                chkbox_html = "<input type='checkbox' name='chkfiles[]' value='%s'>" \
                    % (concat_folder_file(virtualpath, f))
            else:
                chkbox_html = ""
                
            local_filename = (get_shared_file(f) if is_root else concat_folder_file(localpath, f))
            
            if is_dir(local_filename, AllowLink=(OPT_FOLLOW_LINK or is_root)):
                body += self.generate_table_row(i, chkbox_html + "(DIR) " + \
                    self.generate_link(concat_folder_file(virtualpath, f)) \
                    , "", "")
                i += 1
        
        # list files
        for f in fileList:
            if ShowCheckbox:
                chkbox_html = "<input type='checkbox' name='chkfiles[]' value='%s'>" \
                    % (concat_folder_file(virtualpath, f))
            else:
                chkbox_html = ""
            
            local_filename = (get_shared_file(f) if is_root else concat_folder_file(localpath, f))
            if is_file(local_filename): # is file
                last_modified = self.date_time_string(os.path.getmtime(local_filename))
                body += self.generate_table_row(i, chkbox_html + \
                    self.generate_link(concat_folder_file(virtualpath, f)) \
                    , human_readable_size(os.path.getsize(local_filename))
                    , last_modified)
                i += 1
                
        body += "</table>"
                              
        return body

    def generate_folder_listing(self, virtualpath, localpath, DownloadMode=False):
        """ Generate the file listing HTML for a folder. """
                
        sep = "&nbsp;&nbsp;&nbsp;"

        body = "<form name='frmfiles' action='%s?r=%s' method='POST'>" \
                % (DOWNLOAD_TAR_PREFIX, PREFIX + virtualpath)
                
        if DownloadMode: # Show download button
            body += "<input type='submit' name='download_tar' value='Download Tar'/>"
            body += sep + "<a href='%s'>Back</a>" % (PREFIX + virtualpath) + "<br>"
        else:   # Show navigation links and current path.
            body += self.generate_parent_link(virtualpath) + sep + \
                self.generate_home_link()
            if OPT_ALLOW_DOWNLOAD_TAR:
                body += sep + self.generate_dlmode_link(virtualpath)
            body += "<br>"
        
        body += virtualpath + "<hr>"
        
        allow_link = (OPT_FOLLOW_LINK or strip_suffix(virtualpath) == "/")
        if len(localpath) == 0 or is_dir(localpath, AllowLink=allow_link):
            body += self.list_files(virtualpath, localpath, ShowCheckbox=DownloadMode)
            
        body += "<hr>"
        body += "</form>"

        return generate_folder_listing_html(body)
    
    def get_param(self, key):
        if key in self.__params:
            return self.__params[key]
        else:
            return None
    
    def parse_params(self):
        """ Parse the parameters from url and request body """

        # init the params
        self.__params = {}

        # get the params in query string
        if self.path.find('?') != -1:
            self.path, qs = self.path.split("?", 1)

            for pair in qs.split("&"):
                try:
                    key, value = pair.split("=")
                except Exception:
                    key, value = (pair, "")
                self.__params[key] = value


def parse_command_line():
    """ Parse command line option subroutine """
    global OPT_PORT, OPT_FOLLOW_LINK, PREFIX, OPT_ALLOW_DOWNLOAD_TAR
    
    parser = argparse.ArgumentParser(
            description="Share your files across the Internet.")
    parser.add_argument('file', type=str, nargs="+",
                        help="file or directory to be shared")
    parser.add_argument('-p', '--port', type=int, default=OPT_PORT,
                        help="the port to listen on")
    parser.add_argument('-f', '--follow-link', action="store_true", default=OPT_FOLLOW_LINK,
                        help="follow symbolic links when listing files; disabled by default")
    parser.add_argument('-a', '--enable-tar', action="store_true", default=OPT_ALLOW_DOWNLOAD_TAR,
                        help="enable remote user to download mutiple files at once in a tar archive")
    
    args = parser.parse_args()
    
    for f in args.file:
        if os.path.exists(f): # TODO: deal with duplicate filenames and files.
            abspath = os.path.abspath(f)
            add_shared_file(key=os.path.basename(abspath), path=abspath)
    OPT_PORT = args.port
    OPT_FOLLOW_LINK = args.follow_link
    OPT_ALLOW_DOWNLOAD_TAR = args.enable_tar
    
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

