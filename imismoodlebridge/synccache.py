import sqlite3
import json
import time
import logging
from iMISpy import openAPI

# use implied rowid
PANELSOURCE_TABLE = "CREATE TABLE panelsource(expires REAL, json TEXT)"
USERUPDATE_TABLE = "CREATE TABLE userupdate(imisid INTEGER PRIMARY KEY, expires REAL)"
FULLUPDATE_TABLE = "CREATE TABLE fullupdate(expires REAL)"
# This MUST be in table create order (probably)...
PANELSOURCE_ROW = "INSERT OR REPLACE INTO panelsource VALUES(:expires, :json) WHERE rowid=1"
USERUPDATE_ROW = "INSERT OR REPLACE INTO userupdate VALUES(:imisid, :expires)"
FULLUPDATE_ROW = "INSERT OR REPLACE INTO fullupdate VALUES(:expires) WHERE rowid=1"
# select...
PANELSOURCE_SELECT = "SELECT * FROM panelsource WHERE rowid=1;"
USERUPDATE_SELECT = "SELECT * FROM userupdate WHERE imisid=?;"
FULLUPDATE_SELECT = "SELECT * FROM fullupdate WHERE rowid=1;"

class CacheDB:
    def __init__(self, config, dbpath):
        self.db = sqlite3.connect(dbpath)
        self.config = config
        self.db.row_factory = sqlite3.Row
        # check if tables exist:
        for table in ("panelsource", "userupdate", "fullupdate"):    
            res = self.db.execute(f'''SELECT name FROM sqlite_master WHERE type='table' AND name='{table}';''').fetchone()
            if res is None:
                logging.debug(f"{table} Table not found, creating.")
                self.db.execute(f"{table.upper()}_TABLE")
                self.db.commit()

    def acquirePanelSourceData(self):
        # get panel source data
        logging.debug("Cache - Refreshing panel data")
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
        for item in api.apiIterator("query", [["QueryName", self.config["iMIS_PANELSOURCE_IQA"] ]]):
            imisside = item["IMIS_SIDE"]
            moodleside = item["MOODLE_SIDE"]
            if imisside in imisCodeToGroup:
                pd["CMap"][imisCodeToGroup[imisside]] = moodleside
                pd["imisgroupcode"][imisCodeToGroup[imisside]] = imisside
        self.db.execute(PANELSOURCE_ROW, (t, json.dumps(pd)))
        self.db.commit()
        return {"expires": t, "json": pd}

    def getPanelSource(self):
        data = self.db.execute(PANELSOURCE_SELECT).fetchone()
        if data is None: data = self.acquirePanelSourceData()
        else: data["json"] = json.loads(data["json"])
        if data["expires"] > time.time():
            data = self.acquirePanelSourceData()
        return data["json"]

    def updateUser(self, imisid):
        t = time.time()+self.config.get("USER_CACHE_TIME", 30)
        self.db.execute(USERUPDATE_ROW, (imisid, t))
        self.db.commit()
    
    def isUserExpired(self, imisid):
        data = self.db.execute(USERUPDATE_SELECT, (imisid,)).fetchone()
        if data is None or time.time() > data["expires"]: return True
        else: return False

    def updatePostion(self, pos):
        self.db.execute(USERUPDATE_ROW, (pos,))
        self.db.commit()