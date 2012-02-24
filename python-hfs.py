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
# the prefix to add before the root directory
# For example, if PREFIX is "/root" and the host is 127.0.0.1, then
# the root directory is http://127.0.0.1/root
PREFIX = "/files"
DOWNLOAD_TAR_PREFIX = "/download_tar"
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

CSS_UPLOAD = """
body { font: 0.8em/1em "trebuchet MS", arial, sans-serif; color: #777; }
h1 { font-size: 1.6em; margin: 30px 0; padding: 0; }
h2 { font-size: 1.4em; padding: 0 0 6px 0; margin: 0;
    border-bottom: solid 1px #ccc; }
h3 { font-size: 1.2em; margin: 0 0 10px 0; padding: 0; }
p { margin: 0; padding: 0; }
form { padding: 0 0 30px 0; }
#wrap { width: 800px; margin: 0 auto; }
#fileDrop { width: 360px; height: 300px; border: dashed 2px #ccc;
    background-color: #fefefe; float: left; color: #ccc; }
#fileDrop p { text-align: center; padding: 125px 0 0 0; font-size: 1.6em; }
#files { margin: 0 0 0 400px; width: 356px; padding: 20px 20px 40px 20px;
    border: solid 2px #ccc; background: #fefefe; min-height: 240px;
    position: relative; }
#fileDrop, #files { -moz-box-shadow: 0 0 20px #ccc; }
#fileList { list-style: none; padding: 0; margin: 0; }
#fileList li { margin: 0; padding: 10px 0; margin: 0; overflow: auto;
    border-bottom: solid 1px #ccc; position: relative; }
#fileList li img { width: 120px; border: solid 1px #999; padding: 6px;
    margin: 0 10px 0 0; background-color: #eee; display: block; float: left; }
#reset { position: absolute; top: 10px; right: 10px; color: #ccc;
    text-decoration: none; }
#reset:hover { color: #333; }
#remove { color: #ccc; text-decoration: none; float:right; }
#remove:hover { color: #333; }
#upload { color: #fff; position: absolute; display: block;
    bottom: 10px; right: 10px; width: auto; background-color: #777;
    padding: 4px 6px; text-decoration: none; font-weight: bold;
    -moz-border-radius: 6px; }
#upload:hover { background-color: #333; }
.loader { position: absolute; bottom: 10px; right: 0; color: orange; }
.loadingIndicator { width: 0%; height: 2px; background-color: orange;
    position: absolute; bottom: 0; left: 0; }
"""

JS_FILEAPI = """
function FileAPI (t, d, f) {
    var fileList = t, fileField = f, dropZone = d, fileQueue = new Array(), preview = null;
    var STATUS_TRANSFERRING = "tr", STATUS_QUEUE = "qu", STATUS_FINISHED = "fi";
    var id_count = 0;
    this.init = function () {
        fileField.onchange = this.addFiles;
        dropZone.addEventListener("dragenter",  this.stopProp, false);
        dropZone.addEventListener("dragleave",  this.dragExit, false);
        dropZone.addEventListener("dragover",  this.dragOver, false);
        dropZone.addEventListener("drop",  this.showDroppedFiles, false);
    }
    this.addFiles = function () {
        addFileListItems(this.files);
    }
    this.showDroppedFiles = function (ev) {
        ev.stopPropagation();
        ev.preventDefault();
        var files = ev.dataTransfer.files;
        addFileListItems(files);
    }
    this.clearList = function (ev) { // Remove all items except the one being uploaded.
        ev.preventDefault();
        for (var i=0; i<fileList.childNodes.length; i++) {
            var node = fileList.childNodes[i];
            if (itemGetStatus(node) != STATUS_TRANSFERRING) {
                itemRemove(node);
                i--;
            }
        }
    }
    this.dragOver = function (ev) {
        ev.stopPropagation();
        ev.preventDefault();
        this.style["backgroundColor"] = "#F0FCF0";
        this.style["borderColor"] = "#3DD13F";
        this.style["color"] = "#3DD13F"
    }
    this.dragExit = function (ev) {
        ev.stopPropagation();
        ev.preventDefault();
        dropZone.style["backgroundColor"] = "#FEFEFE";
        dropZone.style["borderColor"] = "#CCC";
        dropZone.style["color"] = "#CCC"
    }
    this.stopProp = function (ev) {
        ev.stopPropagation();
        ev.preventDefault();
    }
    this.uploadQueue = function (ev) {
        ev.preventDefault();
        for (var index in fileList.childNodes) {
            node = fileList.childNodes[index];
            if (itemGetStatus(node) == STATUS_TRANSFERRING)
                return; // Only upload one file at a time.
        }
        if (fileQueue.length > 0) {
            uploadNextFile();
        } else {
            alert("Please select at least a file to upload");
        }
    }
    var generateID = function() {
        return (++id_count).toString() + Math.floor(Math.random()*10000).toString();
    }
    var generateInvisibleDivWithText = function(text) {
        var div = document.createElement("div");
        div.style["display"] = "none";
        div.innerHTML = text;
        return div;
    }
    var hideElement = function(name) {
        document.getElementById(name).style["display"] = "none";
    }
    var uploadNextFile = function() {
        var item = fileQueue.shift();
        var p = document.createElement("p");
        p.className = "loader";
        var pText = document.createTextNode("Uploading...");
        p.appendChild(pText);
        item.li.appendChild(p);
        uploadFile(item.file, item.li);
    }
    var size2str = function (nsize) {
        var KILO = 1024, MEGA = KILO * 1024, GIGA = MEGA * 1024;
        if (nsize > Math.pow(10,9)) return (nsize / GIGA).toFixed(2) + " GiB";
        if (nsize > Math.pow(10,6)) return (nsize / MEGA).toFixed(2) + " MiB";
        if (nsize > Math.pow(10,3)) return (nsize / KILO).toFixed(2) + " KiB";
        return nsize + " B";
    }
    var addFileListItems = function (files) {
        for (var i = 0; i < files.length; i++) {
            showFileInList(files[i]);
        }
    }
    var itemGetStatus = function (li) {
        return li.getElementsByTagName("div")[1].innerHTML;
    }
    var itemSetStatus = function (li, st) {
        return li.getElementsByTagName("div")[1].innerHTML = st;
    }
    var itemGetID = function(li) {
        return li.getElementsByTagName("div")[2].innerHTML;
    }
    var itemRemove = function(li) {
        var id = itemGetID(li);
        fileList.removeChild(li);
        for (var index in fileQueue)
            if (fileQueue[index].id == id)
                fileQueue.splice(index, 1); // remove fileQueue[index]
    }
    var showFileInList = function (file) {
        if (file) {
            var li = document.createElement("li");
            var h3 = document.createElement("h3");
            var h3Text = document.createTextNode(file.name);
            h3.appendChild(h3Text);
            var aRemove = document.createElement("a");
            aRemove.href = "#"; aRemove.innerHTML = "Remove"; aRemove.id = "remove";
            aRemove.onclick = function (ev) {
                if (itemGetStatus(li) != STATUS_TRANSFERRING) itemRemove(li);
            }
            h3.appendChild(aRemove);
            li.appendChild(h3)
            var p = document.createElement("p");
            var pText = document.createTextNode(
                size2str(file.size)
            );
            p.appendChild(pText);
            li.appendChild(p);
            var divLoader = document.createElement("div");
            divLoader.className = "loadingIndicator";
            li.appendChild(divLoader);
            var id = generateID();
            li.appendChild(generateInvisibleDivWithText(STATUS_QUEUE));
            li.appendChild(generateInvisibleDivWithText(id));
            fileList.appendChild(li);
            fileQueue.push({file : file, li : li, id : id});
        }
    }
    var updateStatus = function (li, loaded, total) {
        var loader = li.getElementsByTagName("div")[0];
        var status = li.getElementsByTagName("p")[0];
        loader.style["width"] = (loaded / total) * 100 + "%";
        status.textContent = size2str(loaded) + "/" + size2str(total)
    }
    var uploadFile = function (file, li) {
        if (li && file) {
            var xhr = new XMLHttpRequest(),
                upload = xhr.upload;
            upload.addEventListener("progress", function (ev) {
                if (ev.lengthComputable) {
                    updateStatus(li, ev.loaded, ev.total);
                }
            }, false);
            upload.addEventListener("load", function (ev) {
                var succeed = (xhr.status == 200);
                var ps = li.getElementsByTagName("p");
                var div = li.getElementsByTagName("div")[0];
                div.style["width"] = "100%";
                div.style["backgroundColor"] = "#0f0";
                for (var i = 0; i < ps.length; i++) {
                    if (ps[i].className == "loader") {
                        ps[i].textContent = succeed ? "Upload complete" : "Upload failed";
                        ps[i].style["color"] = succeed ? "#3DD13F" : "#FF0000";
                        break;
                    }
                }
                if (ev.lengthComputable) {
                    updateStatus(li, succeed ? ev.loaded : 0, ev.total);
                }
                itemSetStatus(li, STATUS_FINISHED);
                uploadNextFile();
            }, false);
            var data = new FormData();
            data.append("filename", file);
            upload.addEventListener("error", function (ev) {console.log(ev);}, false);
            xhr.open("POST", upload_post_url, true);
            xhr.setRequestHeader("Cache-Control", "no-cache");
            xhr.setRequestHeader("X-Requested-With", "XMLHttpRequest");
            xhr.setRequestHeader("X-File-Name", file.name);
            xhr.send(data);
            li.getElementsByTagName("a")[0].onclick = function(ev) { // "remove" button
                var msg = "Are you sure you want to remove the item being uploaded?";
                if (itemGetStatus(li) != STATUS_TRANSFERRING || confirm(msg)) {
                    xhr.abort();
                    itemRemove(li);
                    uploadNextFile();
                }
            }
            itemSetStatus(li, STATUS_TRANSFERRING);
        }
    }    
}
window.onload = function () {
    if (typeof FileReader == "undefined") alert ("Sorry your browser does not support the File API and this demo will not work for you");
    FileAPI = new FileAPI(
        document.getElementById("fileList"),
        document.getElementById("fileDrop"),
        document.getElementById("fileField")
    );
    FileAPI.init();
    var reset = document.getElementById("reset");
    reset.onclick = FileAPI.clearList;
    var upload = document.getElementById("upload");
    upload.onclick = FileAPI.uploadQueue;
}
"""

UPLOAD_TEMPLATE = """
<!DOCTYPE html>
<!--
    This upload form is modified from Phil's Ajax & XMLHttpRequest
    file upload demo. You can find the original post here:
    http://www.profilepicture.co.uk/tutorials/ajax-file-upload-xmlhttprequest-level-2/
-->
<html>
    <head>
        <meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
        <style type="text/css">
            %(CSS_UPLOAD)s
        </style>
        <title>FileAPI nad XHR 2 ajax uploading</title>
    </head>
    <body>
        <div id="wrap">            

            <form id="fileForm" action="" method="post" enctype="multipart/form-data">
                <h1>Choose (multiple) files or drag them onto drop zone below</h1>
                <input type="file" id="fileField" name="fileField" multiple />
            </form>

            <div id="fileDrop">
                <p>Drop files here</p>
            </div>

            <div id="files">
                <h2>File list</h2>
                <a id="reset" href="#" title="Remove all files from list">Clear list</a>
                <ul id="fileList"></ul>
                <a id="upload" href="#" title="Start uploading files in list">Start uploading</a>
            </div>
        </div>
        <script language="javascript">
            var upload_post_url = "%(UPLOAD_URL)s";
            %(JS_FILEAPI)s
        </script>
    </body>
</html>
"""
def generate_upload_html():
    return UPLOAD_TEMPLATE % \
        {"UPLOAD_URL": UPLOAD_PREFIX, "CSS_UPLOAD": CSS_UPLOAD \
         , "JS_FILEAPI": JS_FILEAPI};


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
        
        if len(PREFIX) == 0 or prefix(path) == PREFIX:
            """ Handle Virtual Filesystem """
            # strip path with PREFIX
            if len(PREFIX) != 0:
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
                
        elif path == "/": # redirect '/' to /PREFIX
            self.send_html(generate_redirect_html(PREFIX))
        elif self.server.OPT_ALLOW_DOWNLOAD_TAR and path == DOWNLOAD_TAR_PREFIX:
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
        
        if self.server.OPT_ALLOW_DOWNLOAD_TAR and path == DOWNLOAD_TAR_PREFIX:
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
                    generate_redirect_html(DOWNLOAD_TAR_PREFIX + "?id=" + retrieve_code
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
            self.send_html("<html><body>Successfully uploaded %s</body></html>" \
                           % (filename), HTTP_OK)
        else:
            WRITE_LOG("Failed to receive file: %s" % (filename), client_addr)
            self.send_html("<html><body>Failed to upload %s</body></html>" \
                           % (filename), HTTP_NOTFOUND)
        
        self.rfile.read(blength) # discard the remaining contents
        
    def save_received_file(self, filename, rfile, length):
        fullpath = concat_folder_file(self.server.UPLOAD_PATH, filename)
        try:
            with open(fullpath, "wb") as f:
                left = length
                writer = RateLimitingWriter(f, 1024) # TEST
                while left > 0:
                    size = min(RECEIVE_CHUNK_SIZE, left)
                    writer.write(rfile.read(size))
                    left -= size
        except Exception, e:
            DEBUG("Save File Exception: " + str(e))
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
    
    def send_text(self, content, format=None, response=HTTP_OK):
        if not format:
            format = "plain"
        self.send_response(response)
        self.send_header("Content-Type", "text/%(FORMAT)s;charset=%(ENCODING)s"
                    % {"FORMAT": format, "ENCODING": get_system_encoding()})
        self.send_no_cache_header()
        self.end_headers()
        self.wfile.write(content)
        
    def send_html(self, content, response=HTTP_OK):
        self.send_text(content, "html", response)
        
    def send_xml(self, content, response=HTTP_OK):
        self.send_text(content, "xml", response)
            
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
                % (DOWNLOAD_TAR_PREFIX, PREFIX + virtualpath)
                
        if DownloadMode: # Show download button
            body += "<input type='submit' name='download_tar' value='Download Tar'/>"
            body += sep + "<a href='%s'>Back</a>" % (PREFIX + virtualpath) + "<br>"
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
