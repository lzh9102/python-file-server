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
import re
from datetime import datetime

TRANSMIT_CHUNK_SIZE = 1024
RECEIVE_CHUNK_SIZE = 1024
UPLOAD_PREFIX = "/upload"

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

def multipart_boundary_length(content_type):
    """ Parse the content-type field and return the boundary length. """
    match = re.search(r'boundary=(--*[0-9a-z][0-9a-z]*)', content_type, re.I)
    if match:
        return len(match.group(1))
    else:
        return 0

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
            end = index + TRANSMIT_CHUNK_SIZE
            end = (length if end > length else end)
            self.__file.write(data[index:end])
            nleft -= TRANSMIT_CHUNK_SIZE
            index += TRANSMIT_CHUNK_SIZE
            self.__limiter.limit()
            
class IntervalTimer:
    """ Measure the time interval between reset() and elapsed() """
    def __init__(self):
        self.reset()
        pass
    def reset(self):
        self.__prevtime = time.time()
    def elapsed(self):
        """ Return the elapsed time in milliseconds. """
        return (time.time() - self.__prevtime) * 1000

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

UPLOAD_TEMPLATE = """
<html class="html">
    <head>
    <title>Uploading</title>
    <script language="javascript">
    var prog_width = %(BAR_WIDTH)d, prog_height = %(BAR_HEIGHT)d;
    var refresh_interval = 1000, curr_bytes = 0, prev_bytes = 0, req_count = 0;
    var file_name = "", transfer_rate = 0, upload_finished = false;
    var prog_color = "red";
    var KILO = 1024, MEGA = KILO * 1024, GIGA = MEGA * 1024;
    function size2str(nsize) { // convert size to human-readable string
        if (nsize > Math.pow(10,9)) return (nsize / GIGA).toFixed(2) + " GiB";
        if (nsize > Math.pow(10,6)) return (nsize / MEGA).toFixed(2) + " MiB";
        if (nsize > Math.pow(10,3)) return (nsize / KILO).toFixed(2) + " KiB";
        return nsize + " B";
    }
    function sec2str(seconds) {        
        var h = Math.floor(seconds / 3600);
        var m = Math.floor(seconds %% 3600 / 60);
        var s = Math.floor(seconds %% 3600 %% 60);
        return ((h > 0 ? h + ":" : "") + (m > 0 ? (h > 0 && m < 10 ? "0" : "") + m + ":" : "0:") + (s < 10 ? "0" : "") + s);
    }
    function trim(str) {
        if (typeof(str) != 'undefined' && str != null)
            return str.replace(/^\s+|\s+$/g,"");
        else
            return ""
    }
    function newXMLHttpObject() {
        var xmlhttp = null;
        if (window.XMLHttpRequest) {
            xmlhttp = new XMLHttpRequest();
        } else if (window.ActiveXObject) {
            if (navigator.userAgent.toUpperCase().indexOf("MSIE 5") != -1)
                xmlhttp = new ActiveXObject("Microsoft.XMLHTTP");
            else
                xmlhttp = new ActiveXObject("Msxml2.XMLHTTP");
        } else { }
        return xmlhttp;
    }
    function refreshTransferRate() {
        transfer_rate = (curr_bytes - prev_bytes) / (refresh_interval / 1000);
        prev_bytes = curr_bytes;
        setTimeout("refreshTransferRate()", refresh_interval);
    }
    function refreshProgress(filename, total, bytes) {
        var perc = bytes / total;
        var rate = transfer_rate;
        document.getElementById("bar").style.width = prog_width * perc;
        document.getElementById("percentage").innerHTML = 
            "<html><body>" + (perc*100).toFixed(1) + "&#37;</body></html>";
        
        document.getElementById("status").innerHTML = "<html><body>"
            + [ "Uploading File: ", filename, "<br>"
               , size2str(bytes), "/", size2str(total)
               , "  (", size2str(rate), "/s)"].join("")
            + "</body></html>";
        curr_bytes = bytes;
    }
    function switchView() {
        document.getElementById("send").style.display = "none";
        document.getElementById("filename").style.display = "none";
        document.getElementById("border").style.display = "inline";
        document.getElementById("bar").style.display = "inline";
        document.getElementById("percentage").style.display = "inline";
        document.getElementById("status").style.display = "inline";  
    }
    function submitForm() {
        var data = new FormData(document.getElementById("form"));
        var req = newXMLHttpObject();
        req.upload.onprogress = function(event) {
            if (event.lengthComputable) {
                var bytes = event.loaded;
                var total = event.total;
                refreshProgress(file_name, total, bytes);
            }
        };
        req.onload = function(event) {
            if (event.lengthComputable) {
                var bytes = event.loaded;
                var total = event.total;
                refreshProgress(file_name, total, bytes);
            }
        }
        req.open("POST", "%(UL_PREFIX)s", true);
        req.send(data);
        refreshTransferRate();
    }
    function button_click() {
        if (document.getElementById("filename").value == "") {
            alert("Please select a file to upload.");
        } else {
            file_name = document.getElementById("filename").value;
            switchView();
            submitForm();
        }
    }
    function initProgressBar() {
        document.write('<div id="border" style="position: absolute'
            + ';width: ' + prog_width + 'px'
            + ';height: ' + prog_height + 'px'
            + ';border: 1px solid black; display: none">');
        document.write('<div id="bar" style="position: absolute'
            + ';width: 0px'
            + ';height: ' + prog_height + 'px'
            + ';background-color: ' + prog_color
            + ';display: none">');
        document.write('</div>'); // close bar div
        document.write('<div id="percentage" style="position: absolute'
            + ';width: ' + prog_width + 'px'
            + ';height: ' + prog_height + 'px'
            + ';text-align: center'
            + ';vertical-align: middle'
            + ';display: none">0.0&#37;</div>');
        document.write('</div>'); // close border div
        document.write("<br><br><br>");
        document.write('<div id="status" style="display: none"></div>');
    }
    </script>
    </head>
    <body>
    <form id="form" name="upload_form" method="post" enctype="multipart/form-data">
    <input id="filename" type="file" name="uploadfile">
    <input id="send" type="button" value="Send" onclick="button_click()">
    <script language="javascript">
        initProgressBar();
    </script>
    </form>
    </body>
</html>
"""
def generate_upload_html():
    return UPLOAD_TEMPLATE % \
        {"UL_PREFIX" : UPLOAD_PREFIX, "BAR_WIDTH": 500, "BAR_HEIGHT": 20}

# HTTP Reply
HTTP_OK = 200
HTTP_NOCONTENT = 204
HTTP_NOTFOUND = 404
HTTP_MOVED_PERMANENTLY = 301

class HttpFileServer(ThreadingMixIn, BaseHTTPServer.HTTPServer):
    
    def __init__(self, server_address, RequestHandlerClass):
        BaseHTTPServer.HTTPServer.__init__(self, server_address, RequestHandlerClass)
        ###### Options and default values ######

        # whether to follow symlink folders
        self.OPT_FOLLOW_LINK = False
        
        # file transmission rate limit in bytes
        self.OPT_RATE_LIMIT = 1024 * 1024 * 10
        
        # whether to allow downloading as archive
        self.OPT_ALLOW_DOWNLOAD_TAR = False
        
        # the prefix to add before the root directory
        # For example, if PREFIX is "/root" and the host is 127.0.0.1, then
        # the root directory is http://127.0.0.1/root
        self.PREFIX = "/files"
        self.DOWNLOAD_TAR_PREFIX = "/download_tar"
        
        # The list of files appearing in the root of the virtual filesystem.
        # TODO: Implement locking to protect concurrent access.
        self.SHARED_FILES = {}
        
        # The directory to save the uploaded files.
        # If the upload path is None, uploading will be disabled.
        self.UPLOAD_PATH = None
        
        self.DOWNLOAD_UUID = {} # map uuid to filelist
        self.DOWNLOAD_UUID_LOCK = threading.Lock()
        
    def add_shared_file(self, key, path):
        final_key = key
        index = 2
        while self.SHARED_FILES.has_key(final_key): # Append an index if the filename alreaady exists.
            final_key = "%s (%d)" % (key, index)
            index += 1
        self.SHARED_FILES[final_key] = path
        
    def get_shared_file(self, key):
        if key not in self.SHARED_FILES:
            return ""
        else:
            return self.SHARED_FILES[key]
    
    def remove_shared_file(self, key):
        try:
            self.SHARED_FILES.pop(key)
        except Exception:
            pass
    
    def get_shared_files(self):
        return self.SHARED_FILES.keys()
    

    def push_download(self, fileList, uuid):
        with self.DOWNLOAD_UUID_LOCK:
            self.DOWNLOAD_UUID[uuid] = fileList
        
    def pop_download(self, uuid):
        with self.DOWNLOAD_UUID_LOCK:
            if uuid in self.DOWNLOAD_UUID: # return and remove the download request
                fileList = self.DOWNLOAD_UUID[uuid]
                self.DOWNLOAD_UUID.pop(uuid)
                return fileList
            else:
                return []

class MyServiceHandler(SimpleHTTPRequestHandler):
    """ This class provides HTTP service to the client """
    
    def __init__(self, request, client_address, server):
        SimpleHTTPRequestHandler.__init__(self, request, client_address, server)

    def log_message(self, format, *args):
        DEBUG("HTTP Server: " + (format % args))
        
    def do_GET(self):
        """ Handle http GET request from client. """
        path = urllib.unquote(self.path)
        
        DEBUG("HTTP GET Request: " + path)
        
        self.parse_params()
        path = path.split("?")[0] # strip arguments from path
        
        if len(self.server.PREFIX) == 0 or prefix(path) == self.server.PREFIX:
            """ Handle Virtual Filesystem """
            # strip path with self.server.PREFIX
            if len(self.server.PREFIX) != 0:
                path = strip_prefix(path)
            
            localpath = self.get_local_path(path)
            DEBUG("localpath: " + localpath)
            
            allow_link = (self.server.OPT_FOLLOW_LINK or strip_suffix(path) == "/")
            if path == "/" or is_dir(localpath, AllowLink=allow_link):
                """ Handle directory listing. """
                DEBUG("List Dir: " + localpath)
                is_download_mode = self.server.OPT_ALLOW_DOWNLOAD_TAR and (self.get_param("dlmode") == "1")
                content = self.generate_folder_listing(path, localpath, is_download_mode)
                self.send_html(content)
                
            elif is_file(localpath):
                """ Handle file downloading. """
                DEBUG("Download File: " + localpath)
                client = self.address_string()
                
                try:                    
                    WRITE_LOG("Start Downloading %s" % (path), client)
                    
                    t0 = time.time()
                    size = self.send_file(localpath, RateLimit=self.server.OPT_RATE_LIMIT)
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
                
        elif path == "/": # redirect '/' to /self.server.PREFIX
            self.send_html(generate_redirect_html(self.server.PREFIX))
        elif self.server.OPT_ALLOW_DOWNLOAD_TAR and path == self.server.DOWNLOAD_TAR_PREFIX:
            self.send_tar_download(self.get_param("id"))
        elif self.server.UPLOAD_PATH and path == UPLOAD_PREFIX:
            self.send_html(generate_upload_html())
        else: # data file
            self.send_response(HTTP_NOTFOUND, "Not Found")
            
    def do_POST(self):
        path = urllib.unquote(self.path)
        
        DEBUG("HTTP POST Request: " + path)
        
        self.parse_params()
        path = path.split("?")[0] # strip arguments from path
        
        if self.server.OPT_ALLOW_DOWNLOAD_TAR and path == self.server.DOWNLOAD_TAR_PREFIX:
            """ handle client downloading tar archive """
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
                self.server.push_download(fileList, retrieve_code)
                self.send_html(
                    generate_redirect_html(self.server.DOWNLOAD_TAR_PREFIX + "?id=" + retrieve_code
                                           , body=redirect_html_body))
            else:
                self.send_html(generate_redirect_html(virtualpath))
        elif self.server.UPLOAD_PATH and path == UPLOAD_PREFIX: # new upload
            """ handle client uploading file """
            self.receive_post_multipart_file()

    def receive_post_multipart_file(self):
        blength = multipart_boundary_length(self.headers.dict["content-type"])
        if blength == 0: # incorrect header
            return
        blength += 8
        flength = int(self.headers.dict["content-length"])
        filename = "received-" + str(datetime.now())
        while 1: # skip header
            line = self.rfile.readline()
            flength -= len(line)
            if line.upper().startswith("CONTENT-DISPOSITION:"):
                match = re.search("filename=\"([^\"]*)\"", line, re.I)
                if match:
                    filename = match.group(1)
            if line == "\r\n":
                break

        flength -= blength
        
        client_addr = self.address_string()
        
        WRITE_LOG("Start receiving file: %s, length: %d"
                  % (filename, flength), client_addr)
        
        if self.save_received_file(filename, self.rfile, flength):
            WRITE_LOG("Successfully received file: %s" % (filename), client_addr)
            self.send_html("<html><body>Transfer Complete</body></html>")        
        else:
            WRITE_LOG("Failed to receive file: %s" % (filename), client_addr)
            self.send_html("<html><body>Transfer Failed</body></html>")
        
    def save_received_file(self, filename, rfile, length):
        fullpath = concat_folder_file(self.server.UPLOAD_PATH, filename)
        try:
            with open(fullpath, "wb") as f:
                left = length
                writer = RateLimitingWriter(f, 1024) # TEST
                timer = IntervalTimer()
                while left > 0:
                    size = min(RECEIVE_CHUNK_SIZE, left)
                    writer.write(rfile.read(size))
                    left -= size
        except Exception:
            pass
        finally:
            if os.path.getsize(fullpath) != length:
                os.remove(fullpath)
                return False
            else:
                return True
        
    def get_local_path(self, path):
        """ Translate a filename separated by "/" to the local file path. """
        path = posixpath.normpath(path)
        wordList = path.split('/')
        wordList = wordList[1:] # remove the first item because it is always empty
        
        if len(wordList) == 0:
            return ""
        
        root = self.server.get_shared_file(wordList[0])
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
    
    def send_text(self, content, format=None):
        if not format:
            format = "plain"
        self.send_response(HTTP_OK) # redirect
        self.send_header("Content-Type", "text/%(FORMAT)s;charset=%(ENCODING)s"
                    % {"FORMAT": format, "ENCODING": get_system_encoding()})
        self.send_no_cache_header()
        self.end_headers()
        self.wfile.write(content)
        
    def send_html(self, content):
        self.send_text(content, "html")
        
    def send_xml(self, content):
        self.send_text(content, "xml")
            
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
            rate_limit = float(RateLimit) / TRANSMIT_CHUNK_SIZE
            
        writer = RateLimitingWriter(self.wfile, rate_limit)
        
        with open(filename, "rb") as f:
            while 1:
                chunk = f.read(TRANSMIT_CHUNK_SIZE)
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
            rate_limit = float(RateLimit) / TRANSMIT_CHUNK_SIZE
        
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

        fileList = self.server.pop_download(id)
        
        if len(fileList) != 0:
            self.send_tar(fileList, ArchiveName, self.server.OPT_RATE_LIMIT)
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
        
        return "<a href='" + urllib.quote(self.server.PREFIX + parent_dir) + "'>Up</a>"
            
    
    def generate_home_link(self):
        """ Generate link for root directory """
        link = ("/" if len(self.server.PREFIX) == 0 else self.server.PREFIX)
        return "<a href='" + link + "'>Home</a>"
    
    def generate_dlmode_link(self, virtualpath):
        link = self.server.PREFIX + virtualpath + "?dlmode=1"
        return "<a href='" + link + "'>Download Multiple Files</a>"
            
    def generate_link(self, virtualpath, text=None):
        """ Generate html link for a file/folder. """
        text = (suffix(virtualpath) if text == None else text)
        link = self.server.PREFIX + virtualpath
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
            fileList = sorted(self.server.get_shared_files()) # list virtual filesystem root
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
                
            local_filename = (self.server.get_shared_file(f) if is_root else concat_folder_file(localpath, f))
            
            if is_dir(local_filename, AllowLink=(self.server.OPT_FOLLOW_LINK or is_root)):
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
            
            local_filename = (self.server.get_shared_file(f) if is_root else concat_folder_file(localpath, f))
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
                % (self.server.DOWNLOAD_TAR_PREFIX, self.server.PREFIX + virtualpath)
                
        if DownloadMode: # Show download button
            body += "<input type='submit' name='download_tar' value='Download Tar'/>"
            body += sep + "<a href='%s'>Back</a>" % (self.server.PREFIX + virtualpath) + "<br>"
        else:   # Show navigation links and current path.
            body += self.generate_parent_link(virtualpath) + sep + \
                self.generate_home_link()
            if self.server.OPT_ALLOW_DOWNLOAD_TAR:
                body += sep + self.generate_dlmode_link(virtualpath)
            body += "<br>"
        
        body += virtualpath + "<hr>"
        
        allow_link = (self.server.OPT_FOLLOW_LINK or strip_suffix(virtualpath) == "/")
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

if __name__ == "__main__":
    """ Parse command line option """
    OPT_PORT = 80
    OPT_FOLLOW_LINK = False
    PREFIX = "file/"
    OPT_ALLOW_DOWNLOAD_TAR = False
    OPT_UPLOAD_PATH = None
    
    parser = argparse.ArgumentParser(
            description="Share your files across the Internet.")
    parser.add_argument('file', type=str, nargs="+",
                        help="file or directory to be shared")
    parser.add_argument('-p', '--port', type=int, default=OPT_PORT,
                        help="the port to listen on")
    parser.add_argument('-f', '--follow-link', action="store_true", default=OPT_FOLLOW_LINK,
                        help="follow symbolic links when listing files; disabled by default")
    parser.add_argument('--enable-tar', action="store_true", default=OPT_ALLOW_DOWNLOAD_TAR,
                        help="enable remote user to download mutiple files at once in a tar archive")
    parser.add_argument('--upload-path', type=str, default=OPT_UPLOAD_PATH)
    args = parser.parse_args()
    
    FILES = args.file
    OPT_PORT = args.port
    OPT_FOLLOW_LINK = args.follow_link
    OPT_ALLOW_DOWNLOAD_TAR = args.enable_tar
    OPT_UPLOAD_PATH = args.upload_path
    if not PREFIX.startswith('/'):
        PREFIX = '/' + PREFIX
    if PREFIX.endswith('/'):
        PREFIX = PREFIX[0:len(PREFIX)-1]
        
    if OPT_UPLOAD_PATH and not os.path.isdir(OPT_UPLOAD_PATH):
        sys.stderr.write( \
            "Warning: Upload path" + OPT_UPLOAD_PATH + " is not a folder.")
    
    """ server """
    try:
        server = HttpFileServer(('', OPT_PORT), MyServiceHandler)
        server.daemon_threads = True
        
        for f in FILES:
            if os.path.exists(f): # TODO: deal with duplicate filenames and files.
                abspath = os.path.abspath(f)
                server.add_shared_file(key=os.path.basename(abspath), path=abspath)
        
        server.OPT_FOLLOW_LINK = OPT_FOLLOW_LINK
        server.OPT_ALLOW_DOWNLOAD_TAR = OPT_ALLOW_DOWNLOAD_TAR
        server.PREFIX = PREFIX
        server.UPLOAD_PATH = OPT_UPLOAD_PATH
        
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
