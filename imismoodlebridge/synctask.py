import socket
import os
import json
from multiprocessing import Process, Queue, connection
import queue
import time
import datetime
import re
import traceback
from iMISpy import openAPI
import requests
import logging
from logging.handlers import TimedRotatingFileHandler
import signal

from .synccache import CacheDB

GET_MOODLE_ID = re.compile(r"""<KEY name="id">\s*<VALUE>([^<]+)<\/VALUE>\s*<\/KEY>\s*<KEY name="username">\s*<VALUE>([^<]+)<\/VALUE>""")

INSTANCE_PATH = os.getcwd()
SOCKET_PATH = os.path.join(INSTANCE_PATH, "socket")
CONFIG_PATH = os.path.join(INSTANCE_PATH, "config.json")
CACHE_DB_PATH = os.path.join(INSTANCE_PATH, "cache.sqlite")
LOG_PATH = os.path.join(INSTANCE_PATH, "synclog.txt")

CONFIG = json.load(open(CONFIG_PATH, "rb"))

LOGGING_MSG_FORMAT  = '%(asctime)s %(levelname)s [%(name)s] %(message)s'
LOGGING_DATE_FORMAT = '%Y-%m-%d %H:%M:%S'
formatter = logging.Formatter(LOGGING_MSG_FORMAT)

logging.basicConfig(level=logging.WARN, format=LOGGING_MSG_FORMAT, datefmt=LOGGING_DATE_FORMAT)
rootlogger = logging.getLogger(None)
flogger = TimedRotatingFileHandler(LOG_PATH, 'midnight', backupCount=5)
flogger.setFormatter(formatter)
rootlogger.addHandler(flogger)
logger = logging.getLogger(__name__)
logger.setLevel(CONFIG.get("LOG_LEVEL", "WARN"))

class UserReceiver(Process):
    def __init__(self, q):
        super().__init__()
        self.q = q
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)

    def run(self):
        logger.debug("Starting receiver. unlinking socket...")
        try: os.unlink(SOCKET_PATH)
        except FileNotFoundError: pass
        self.sock.bind(SOCKET_PATH)
        self.sock.listen()
        logger.debug("Receiver - listening.")
        while True:
            connection, client_address = self.sock.accept()
            logger.debug(f"Receiver - Connection ({connection}) from ({client_address})")
            data = connection.recv(1024).decode()
            if data == "": 
                logger.debug("Receiver - Received blank, quitting...")
                break
            try: q.put((None, str(int(data))))
            except ValueError: pass
            connection.close()
        self.sock.close()

def createMoodleUsers(users):
    # this will be slow because we cannot batch create multiple users.
    # if you try, and there's a problem with one user, the whole batch call fails. unfortunate.
    postdata = {
            "wstoken": CONFIG["MOODLE_SYNC_TOKEN"],
            "wsfunction": "core_user_create_users"
    }
    for user in users:
        logger.debug(f"CREATING user: %s", user["username"])
        postdata[f"users[0][username]"] = user["username"]
        postdata[f"users[0][auth]"] = "userkey"
        postdata[f"users[0][firstname]"] = "New"
        postdata[f"users[0][lastname]"] = "User"
        postdata[f"users[0][email]"] = user["email"]
        response = requests.post(f"{CONFIG['MOODLE_URL']}/webservice/rest/server.php",
            data=postdata)
        if "EXCEPTION" in response.text:
            logger.debug("ERROR - Postdata: %s", postdata)
    return


def convertUserMoodleID(users):
    postdata = {
            "wstoken": CONFIG["MOODLE_SYNC_TOKEN"],
            "wsfunction": "core_user_get_users_by_field",
            "field" : "username"
    }
    logger.debug(f"Converting users: %s", users)
    for i, user in enumerate(users):
        postdata[f"values[{i}]"] = user
    logger.debug("Post data: %s", postdata)
    response = requests.post(f"{CONFIG['MOODLE_URL']}/webservice/rest/server.php",
        data=postdata).text
    foundusers = {}
    for mid, imisid in GET_MOODLE_ID.findall(response):
        foundusers[imisid] = mid
    logger.debug(f"Got users: %s", foundusers.values())
    return foundusers

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
    logger.debug("Sending enrollment data: %s", postdata)
    requests.post(f"{CONFIG['MOODLE_URL']}/webservice/rest/server.php",
        data=postdata)

def userProcess(cache, api, imisid):
    if not cache.isUserExpired(imisid): 
        logger.debug(f"Skipping user: %s", imisid)
        return
    logger.debug("Processing user: %s", imisid)
    courses = []
    user = None
    for item in api.apiIterator("GroupMember", [["PartyID", imisid]]):
        if item["Group"]["GroupId"] in cache.getPanelSource()["CMap"]: # this checks for literal Group IDs->CourseID
            cids = cache.getPanelSource()["CMap"][item["Group"]["GroupId"]]
            courses.extend(cids.split(","))
            if not user:
                user = {"username": imisid, "email": item["Party"]["Email"] }
    if courses:
        uids = convertUserMoodleID([imisid])
        logger.debug("Got user map: %s", uids)
        if imisid not in uids: 
            createMoodleUsers([user])
            uids = convertUserMoodleID([imisid])
        if imisid in uids:
            uid = uids[imisid]
            logger.debug("Found UID %s for iMIS ID %s", uid, imisid)
            processEnrollments(((uid, cid) for cid in courses))
            cache.updateUser(imisid)
    else:
        logger.debug("No courses to process.")

def fullSync(cache, q):
    synccourses = {} # coursid: [list of groups]
    for gid, cids in cache.getPanelSource()["CMap"].items():
        for ci in cids.split(","):
            if ci in synccourses: synccourses[ci].append(gid)
            else: synccourses[ci] = [gid]
    for cid in synccourses:
        logger.debug("Queuing course sync: %s - %s", cid, synccourses[cid])
        q.put(("course", (cid, synccourses[cid])))
    q.put(("fulldone",None))
    for x in range(CONFIG.get("WORKERS", 2)):
        q.put(None)

def courseSync(api, d):
    # (courseID, [groups])
    logger.debug("Processing course: %s", d)
    users = {}
    for gid in d[1]:
        for imisdata in api.apiIterator("GroupMemberSummary", [["GroupID", gid]]):
            gmimisid = imisdata["Party"]["Id"]
            if gmimisid not in users:
                users[gmimisid] = {
                    "username": gmimisid,
                    "email": imisdata["Party"]["Email"]
                }
    uids = convertUserMoodleID(users.keys())
    missing = []
    for iid in users:
        if iid not in uids:
            missing.append(users[iid])
    if missing:
        createMoodleUsers(missing)
        uids = convertUserMoodleID(users)
    enrollments = []
    for uid in uids.values():
        enrollments.append((uid, d[0]))
    processEnrollments(enrollments)

def userProcessor(q):
    logger.debug("Starting user processor worker...")
    api = openAPI(CONFIG)
    cache = CacheDB(CONFIG, CACHE_DB_PATH)
    data = q.get()
    while data is not None:
        # process user id and or <other thing>
        task, taskdata = data
        logger.debug("Got task (%s), with data (%s)", task, taskdata)
        if task is None: userProcess(cache, api, taskdata)
        elif task == "full": fullSync(cache, q)
        elif task == "fulldone": cache.updateFullSync()
        elif task == "course": courseSync(api, taskdata)
        data = q.get()

if __name__ == '__main__':
    runtime = time.time() + (CONFIG.get("WORKER_DUATION", 2) * 60) + 10
    logger.debug(f"Running for {runtime-time.time():.1f} seconds.")
    q = Queue()
    fs = Queue() # full sync queue
    # check panelsource cache, fetch and update if old.
    db = CacheDB(CONFIG, CACHE_DB_PATH)
    db.getPanelSource() # pre-warm-cache data
    # start receiver process
    orig_sigint_handler = signal.signal(signal.SIGINT, signal.SIG_IGN) # ignore sigINT for now...
    r = UserReceiver(q)
    r.start()
    logger.debug("Starting workers")
    processes = []
    fullsyncprocesses = []
    for x in range(CONFIG.get("WORKERS", 2)):
        p = Process(target=userProcessor, args=(q, ))
        processes.append(p)
        p.start()
    signal.signal(signal.SIGINT, orig_sigint_handler)
    try:
        while time.time() < runtime :
            time.sleep(0.5)
            # if hour of longprocess has passed:
            currenttime = datetime.datetime.now().time()
            if not fullsyncprocesses and currenttime.hour == CONFIG.get("FULLSYNC_HOUR", 4) and currenttime.second > 20 and db.isFullSyncExpired():
                logger.debug("Starting full sync.")
                orig_sigint_handler = signal.signal(signal.SIGINT, signal.SIG_IGN) # ignore sigINT for now...
                for x in range(CONFIG.get("WORKERS", 2)):
                    fp = Process(target=userProcessor, args=(fs, ))
                    fullsyncprocesses.append(fp)
                    fp.start()
                signal.signal(signal.SIGINT, orig_sigint_handler)
                fs.put(("full", None))
        if fullsyncprocesses:
            # wait for full sync if running...
            logger.debug("Waiting for fullsync...")
            connection.wait(fp.sentinel for fp in fullsyncprocesses)
            logger.debug("Full sync done")
            fullsyncprocesses = []

    except KeyboardInterrupt:
        logger.warning("CTRL+C, quitting...")
    except Exception as e: # better quit so no zombie... hope no exceptions later on...
        print("EXCEPTION RAISED")
        traceback.print_exc()
    r.terminate()
    for x in range(CONFIG.get("WORKERS", 2)):
        q.put(None)
    if fullsyncprocesses:
        for x in fullsyncprocesses:
            fs.put(None)
        connection.wait(fp.sentinel for fp in fullsyncprocesses)
    logger.debug("Waiting for processes to exit...")
    connection.wait(p.sentinel for p in processes)
    logger.debug("Emptying queue.")
    #empty queue - just in case, do we even need to do this?
    while True:
        try: q.get_nowait()
        except queue.Empty: break
    logger.debug("Finally quit.")
