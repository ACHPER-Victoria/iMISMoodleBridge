import json
import requests
import logging
log = logging.getLogger()

FIND_BY_USERNAME = {
    "$type": "Asi.Soa.Core.DataContracts.GenericExecuteRequest, Asi.Contracts",
    "OperationName": "FindByUserName",
    "EntityTypeName": "User",
    "Parameters": {
        "$type": "System.Collections.ObjectModel.Collection`1[[System.Object, mscorlib]], mscorlib",
        "$values": [
            {
                "$type": "System.String",
                "$value": ""
            }
        ]
    },
    "ParameterTypeName": {
        "$type": "System.Collections.ObjectModel.Collection`1[[System.String, mscorlib]], mscorlib",
        "$values": [
            "System.String"
        ]
    },
    "UseJson": False
}

def getiMISTokenData(url, clientid, clientsecret, refreshtoken):
    return requests.post(f"{url}/token", data={
        "grant_type": "refresh_token", "client_id": clientid,
        "client_secret": clientsecret, "refresh_token": refreshtoken}).json()

def getiMISUserData(url, username, clientid, access_token):
    headers = { "Authorization": f"Bearer {access_token}", "Content-Type": "application/json" }
    body = json.loads(json.dumps(FIND_BY_USERNAME))
    body["Parameters"]["$values"][0]["$value"] = username
    result = requests.post(f"{url}/api/User/_execute", json=body, headers=headers).json()
    return result["Result"]

def getiMISProfileData(url, userid, access_token):
    headers = { "Authorization": f"Bearer {access_token}", "Content-Type": "application/json" }
    return requests.get(f"{url}/api/Party/{userid}", headers=headers).json()

def findEmail(partyData):
    email = None
    for ie in partyData["Emails"]["$values"]:
        email = ie["Address"]
        if ie["IsPrimary"]: return email
    return email

