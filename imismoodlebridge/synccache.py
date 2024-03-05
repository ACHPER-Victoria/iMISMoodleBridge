import sqlite3
import json
import time
import logging
from iMISpy import openAPI
logger = logging.getLogger(__name__)


# use implied rowid
TABLE = {
    "PANELSOURCE" : "CREATE TABLE panelsource(rowid INTEGER PRIMARY KEY, expires REAL, json TEXT)",
    "USERUPDATE" : "CREATE TABLE userupdate(imisid INTEGER PRIMARY KEY, expires REAL)",
    "FULLSYNC" : "CREATE TABLE fullsync(rowid INTEGER PRIMARY KEY, expires REAL)"
}
# This MUST be in table create order (probably)...
PANELSOURCE_ROW = "INSERT OR REPLACE INTO panelsource VALUES(:rowid, :expires, :json)"
USERUPDATE_ROW = "INSERT OR REPLACE INTO userupdate VALUES(:imisid, :expires)"
FULLSYNC_ROW = "INSERT OR REPLACE INTO fullsync VALUES(:rowid, :expires)"
# select...
PANELSOURCE_SELECT = "SELECT * FROM panelsource WHERE rowid=1;"
USERUPDATE_SELECT = "SELECT * FROM userupdate WHERE imisid=?;"
FULLSYNC_SELECT = "SELECT * FROM fullsync WHERE rowid=1;"

class CacheDB:
    def __init__(self, config, dbpath):
        logger.setLevel(config.get("LOG_LEVEL", "WARN"))
        self.db = sqlite3.connect(dbpath)
        self.paneldata = None
        self.config = config
        self.db.row_factory = sqlite3.Row
        # check if tables exist:
        for table in ("panelsource", "userupdate", "fullsync"):    
            res = self.db.execute(f'''SELECT name FROM sqlite_master WHERE type='table' AND name='{table}';''').fetchone()
            if res is None:
                logger.debug(f"{table} Table not found, creating.")
                self.db.execute(f"{TABLE[table.upper()]}")
                self.db.commit()

    def acquirePanelSourceData(self):
        # get panel source data
        logger.debug("Cache - Refreshing panel data")
        t = time.time() + (self.config.get("PANEL_CACHE_TIME", 10)*60) - 10 # 10 seconds before
        imisCodeToGroup = {}
        imisGroupToCode = {}
        api = openAPI(self.config)
        for item in api.apiIterator("Group", [["GroupClassId", "EVENT"] ]):
            imisCodeToGroup[item["GroupId"]] = item["GroupId"]
            imisGroupToCode[item["GroupId"]] = item["GroupId"]
        for item in api.apiIterator("Group", [["GroupClassId", self.config.get("iMIS_PURCHASED_PRODUCTS_CLASS_ID", "E88E66B1-9516-47F9-88DC-E2EB8A3EF13E")] ]):
            imisCodeToGroup[item["Name"]] = item["GroupId"]
            imisGroupToCode[item["GroupId"]] = item["Name"]
        pd = {}
        pd["CMap"] = {}
        pd["imisgroupcode"] = {} # this isn't used... yet.
        for item in api.apiIterator("query", [["QueryName", self.config["iMIS_PANELSOURCE_IQA"] ]]):
            imisside = item["IMIS_SIDE"]
            moodleside = item["MOODLE_SIDE"]
            if imisside in imisCodeToGroup:
                pd["CMap"][imisCodeToGroup[imisside]] = moodleside
                pd["imisgroupcode"][imisCodeToGroup[imisside]] = imisside
        self.db.execute(PANELSOURCE_ROW, (1, t, json.dumps(pd)))
        self.db.commit()
        self.paneldata = {"expires": t, "json": pd}
        return pd

    def getPanelSource(self):
        if not self.paneldata:
            self.paneldata = self.db.execute(PANELSOURCE_SELECT).fetchone()
            if self.paneldata is not None:
                # weird unpacking...
                self.paneldata = {"expires": self.paneldata["expires"], "json": json.loads(self.paneldata["json"])}
        if self.paneldata is None: 
            return self.acquirePanelSourceData()
        if self.paneldata["expires"] < time.time():
            return self.acquirePanelSourceData()
        return self.paneldata["json"]

    def updateUser(self, imisid):
        t = time.time()+self.config.get("USER_CACHE_TIME", 30)
        self.db.execute(USERUPDATE_ROW, (imisid, t))
        self.db.commit()
    
    def isUserExpired(self, imisid):
        data = self.db.execute(USERUPDATE_SELECT, (imisid,)).fetchone()
        if data is None or time.time() > data["expires"]: return True
        else: return False
    
    def updateFullSync(self):
        # don't allow another full sync within this amount of time (2 hours)
        t = time.time()+(60*60*2)
        self.db.execute(FULLSYNC_ROW, (1, t))
        self.db.commit()
    
    def isFullSyncExpired(self):
        data = self.db.execute(FULLSYNC_SELECT).fetchone()
        if data is None or time.time() > data["expires"]: return True
        else: return False
