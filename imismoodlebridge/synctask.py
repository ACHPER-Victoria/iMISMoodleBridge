import socket
import os
import json
from multiprocessing import Process, Queue
import queue
import time
import datetime
import re
from iMISpy import openAPI
import requests
import logging

from .synccache import CacheDB

GET_MOODLE_ID = re.compile(r"""<KEY name="id">\s+<VALUE>([^<]+)</VALUE>\s+</KEY>\s+<KEY name="username">\s+<VALUE>([^<]+)</VALUE>""")

INSTANCE_PATH = os.path.join(".", "instance")
SOCKET_PATH = os.path.join(INSTANCE_PATH, "socket")
CONFIG_PATH = os.path.join(INSTANCE_PATH, "config.json")
CACHE_DB_PATH = os.path.join(INSTANCE_PATH, "cache.sqlite")
LOG_PATH = os.path.join(INSTANCE_PATH, "synclog.txt")

CONFIG = json.load(open(CONFIG_PATH, "rb"))

LOGGING_MSG_FORMAT  = '%(name)s [%(levelname)s] [%(asctime)s] %(message)s'
LOGGING_DATE_FORMAT = '%Y-%m-%d %H:%M:%S'

logging.basicConfig(level=logging.WARN, format=LOGGING_MSG_FORMAT, datefmt=LOGGING_DATE_FORMAT)
rootlogger = logging.getLogger(None)
rootlogger.setLevel(CONFIG.get("LOG_LEVEL", "WARN"))
logger = logging.handlers.TimedRotatingFileHandler(LOG_PATH, 'midnight', backupCount=5)
rootlogger.addHandler(logger)


class UserReceiver(Process):
    def __init__(self, q):
        self.q = q
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)

    def run(self):
        logging.debug("Starting receiver. unlinking socket...")
        os.unlink(SOCKET_PATH)
        self.sock.bind(SOCKET_PATH)
        self.sock.listen()
        logging.debug("Receiver - listening.")
        while True:
            connection, client_address = self.sock.accept()
            logging.debug(f"Receiver - Connection ({connection}) from ({client_address})")
            data = connection.recv(1024).decode()
            if data == "": 
                logging.debug("Receiver - Received blank, quitting...")
                break
            try: q.put((None, int(data)))
            except ValueError: pass
            connection.close()
        self.sock.close()

def convertUserMoodleID(users):
    postdata = {
            "wstoken": CONFIG["MOODLE_SYNC_TOKEN"],
            "wsfunction": "core_user_get_users_by_field",
    }
    logging.debug(f"Converting users: %s", users)
    for i, user in enumerate(users):
        postdata[f"values[{i}]"] = user
    response = requests.post(f"{CONFIG['MOODLE_URL']}/webservice/rest/server.php",
        data=postdata).text
    users = {}
    for mid, imisid in GET_MOODLE_ID.findall(response):
        users[imisid] = mid
    logging.debug(f"Got users: %s", users.values())
    return users

# enrollments is a list of (moodle user id, moodle course id)
def processEnrollments(enrollments):
    postdata = {
        "wstoken": CONFIG["MOODLE_SYNC_TOKEN"],
        "wsfunction": "enrol_manual_enrol_users",
    }
    for i, e in enumerate(enrollments):
        postdata[f"enrolments[{i}][roleid]"] = CONFIG.get("STUDENT_ROLE_ID", 5)
        postdata[f"enrolments[{i}][userid]"] = e[0]
        postdata[f"enrolments[{i}][courseid]"] = e[1]
    logging.debug("Sending enrollment data: %s", postdata)
    requests.post(f"{CONFIG['MOODLE_URL']}/webservice/rest/server.php",
        data=postdata)

def userProcess(cache, api, imisid):
    if not cache.isUserExpired(): 
        logging.debug(f"Skipping user: %s", imisid)
        return
    logging.debug("Processing user: %s", imisid)
    uids = convertUserMoodleID([imisid])
    if imisid in uids:
        uid = uids[imisid]
        enrollments = []
        for item in api.apiIterator("GroupMember", [["PartyID", imisid]]):
            if item["Group"]["GroupId"] in cache["CMap"]:
                enrollments.append((uid, cache["CMap"][item["Group"]["GroupId"]]))
    processEnrollments(enrollments)
    cache.updateUser(imisid)


def fullSync(cache, q):
    synccourses = {} # coursid: [list of groups]
    for gid, cid in cache["CMap"].items():
        if cid in synccourses: synccourses[cid].append(gid)
        else: synccourses[cid] = [gid]
    for cid in synccourses:
        logging.debug("Queuing course sync: %s - %s", (cid, synccourses[cid]))
        q.put(("course", (cid, synccourses[cid])))

def courseSync(api, d):
    # (courseID, [groups])
    logging.debug("Processing course: %s", d)
    users = set()
    for gid in d[0]:
        for imisdata in api.apiIterator("GroupMemberSummary", [["GroupID", gid]]):
            users.add(imisdata["Party"]["Id"])
    uids = convertUserMoodleID(users)
    enrollments = []
    for uid in uids.values():
        enrollments.append(uid, d[0])
    processEnrollments(enrollments)

def userProcessor(q):
    logging.debug("Starting user processor worker...")
    api = openAPI(CONFIG)
    cache = CacheDB(CONFIG, CACHE_DB_PATH)
    data = q.get()
    while data is not None:
        # process user id and or <other thing>
        task, taskdata = data
        logging.debug("Got task (%s), with data (%s)", (task, taskdata))
        if task is None: userProcess(cache, api, taskdata)
        elif task == "full": fullSync(cache, q)
        elif task == "course": courseSync(api, taskdata)
        data = q.get()

def stopReceiver():
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.connect(SOCKET_PATH)
    client.sendall("".encode())

if __name__ == '__main__':
    runtime = time.time() + (CONFIG.get("WORKER_DUATION", 2) * 60) + 10
    logging.debug(f"Running for {runtime-time.time():.1f} seconds.")
    q = Queue()
    # check panelsource cache, fetch and update if old.
    db = CacheDB(CONFIG, CACHE_DB_PATH)
    db.getPanelSource() # pre-warm-cache data
    # start receiver process
    r = UserReceiver(q)
    r.start()
    logging.debug("Starting workers")
    for x in range(CONFIG.get("WORKERS", 2)):
        Process(target=userProcessor, args=(q, )).start()
    sentFullSync = False
    while time.time() < runtime :
        time.sleep(0.5)
        # if hour of longprocess has passed:
        currenttime = datetime.datetime.now().time()
        if not sentFullSync and currenttime.hour == CONFIG.get("FULLSYNC_HOUR", 4) and currenttime.second > 20:
            logging.debug("Queueing full sync.")
            q.put(("full", None))
            sentFullSync = True
        
    stopReceiver()
    for x in range(CONFIG.get("WORKERS", 2)):
        q.put(None)
    # allow time for quit
    time.sleep(5)
    #empty queue - just in case
    while True:
        try: q.get_nowait()
        except queue.Empty: break
