import time
from flask import Blueprint, jsonify, request, abort
from flask import redirect, current_app
from .imisUtil import getiMISUserData, getiMISTokenData, getiMISProfileData, findEmail
import requests
import logging
import re
import socket
import os

GET_LOGIN_URL = re.compile(r"<VALUE>([^<]+)</VALUE>")

log = logging.getLogger()
bp = Blueprint('oauth2', __name__)

@bp.route('/', methods=('GET', 'POST'))
def home():
    return redirect(current_app.config["HOMEPAGE"])

@bp.route("/update/<imisID>", methods=('GET',))
def updateUser(imisID):
    if len(imisID) > 10:
        # crude attempt to stop overflowing my socket buffer
        return redirect(current_app.config["IMIS_MOODLE_LOGIN_PAGE"])
    # send ID to synctask to check for updated course registrations
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.connect(os.path.join(current_app.instance_path, "socket"))
    client.sendall(imisID.encode())
    client.close()
    response = jsonify(success=True)
    response.headers["Access-Control-Allow-Origin"] = current_app.config['HOMEPAGE']
    return response

# only accept POST from iMIS
@bp.route('/login', methods=('GET', 'POST', 'HEAD'))
def login():
    if request.method == 'HEAD': return {}
    if request.method == 'POST':
        refresh_token = request.form.get('refresh_token')
        config_clientid = current_app.config["IMIS_CLIENT_ID"]
        tokendata = getiMISTokenData(
            current_app.config['HOMEPAGE'],
            config_clientid,
            current_app.config["IMIS_CLIENT_SECRET"],
            refresh_token
        )
        if "userName" not in tokendata:
            log.error(f"Missing Username. TokenData: {tokendata}")
            return redirect(current_app.config['HOMEPAGE'])
        if tokendata["userName"] == "GUEST": 
            log.error("Attempting to login with GUEST access. You should gatekeep your SSO iPart page with authenticated users only.")
            return redirect(current_app.config['HOMEPAGE'])
        #log.error(f"TokenData: {tokendata}")
        try: 
            clientid = tokendata["as:client_id"]
            if clientid != config_clientid:
                log.error(f"ClientID mismatch. Detected clientid: {clientid}, Config clientid: {config_clientid}")
                abort(500)
        except KeyError: 
            log.error(f"Invalid setup - Check clientid and secret. Config ClientID: {config_clientid}, Error: {tokendata}.")
            abort(500)
        imisUserData = getiMISUserData(current_app.config['HOMEPAGE'], tokendata["userName"],
            clientid, tokendata["access_token"])
        if (imisUserData["IsAnonymous"]): return redirect(current_app.config["IMIS_MOODLE_LOGIN_PAGE"])
        
        # get some iMIS user data (first name, last name, email)
        profiledata = getiMISProfileData(current_app.config['HOMEPAGE'], imisUserData["UserId"], tokendata["access_token"])
        postdata = {
            "wstoken": current_app.config["MOODLE_AUTH_TOKEN"],
            "wsfunction": current_app.config["MOODLE_FUNCTION"],
            "user[username]": imisUserData["UserId"],
            "user[firstname]": profiledata["PersonName"]["FirstName"],
            "user[lastname]": profiledata["PersonName"]["LastName"],
            "user[email]": findEmail(profiledata),
        }
        response = requests.post(f"{current_app.config['MOODLE_URL']}/webservice/rest/server.php",
            data=postdata).text
        # send ID to synctask to check for updated course registrations
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            client.connect(os.path.join(current_app.instance_path, "socket"))
            client.sendall(imisUserData["UserId"].encode())
            client.close()
        except FileNotFoundError:
            logging.error("Socket not found, synctask not running. Check cron.")
        # redirect to moodle proper.
        return redirect(GET_LOGIN_URL.search(response).group(1))
    else:
        return redirect(current_app.config["HOMEPAGE"])
