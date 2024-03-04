## Get a cPanel host and install Moodle

## Setup with cPanel
* In cPanel go to Setup Python App
* Create Application


* Download [passenger_wsgi.py](passenger_wsgi.py)
* Using the cPanel file manager, place that file in

## Create Panel Source to manage products/events to autoenroll
* iMIS staff site -> RiSE -> Page Builder -> Manage Content
* Create a new RiSE page that will be your site-site portal to linking products/events to moodle courses
* Make sure the page does not have Everyone access
* Add Content -> Content -> Panel Editor
* Panel -> New Panel
* Panel Name: It doesn't overly matter what you put here. It should be descriptive though like "Moodle_Course_map" or similar
* Panel Name: 
* Parent type: Standalone
* Click Create source. It doesn't overly matter what you put as the name, however it should probably be descriptive and similar to the above name.
* Click "Add property"
* Property name "IMIS_SIDE", TEXT, length 120.
* Add and continue
* Property name "MOODLE_SIDE", Integer
* Add and close
* Drag both new properties to the right hand panel
* Tick "Required" on both items
* Click Save and Close
* Tick "Allow users to " add, delete and edit
* Click OK
* Click Save & Publish
* Add that RiSE page to your Staff site site-map so you can access it from the staff site menu.
* Finally create an IQA so the tool can access this data.
 * RiSE -> IQA
 * Create a new query, make sure you are in Advanced mode, give it a name like the above descriptive name
 * Source tab, click All sources, select the Panel Source you created earlier
 * Display tab, view all columns, tick IMIS_SIDE and MOODLE_SIDE
 * Click Save, OK
 * Click Summary. Note "Path", you must put the Path in ```config.json``` in ```iMIS_PANELSOURCE_IQA```

## Create Moodle WebService to allow sync users
First make sure you have enabled web services. Create a user in Moodle dedicated for syncing users. Make sure that user has the following permissions (you may want to use a Role): 
* moodle/user:viewdetails
* moodle/user:viewhiddendetails
* moodle/course:useremail
* moodle/course:view
* The user must also be able to "Allow role assignments" for student.
Then perform the following:
* Moodle -> Site administration -> Server -> Web services -> External services
* Add, give descriptive name, tick enabled, tick Authorised users only
* Click Add service, then add the following functions:
 * core_user_get_users_by_field
 * enrol_manual_enrol_users
 * enrol_manual_unenrol_users
 * core_user_create_users
* Go back to Site administration -> Server -> Web services -> External services
* Click Authorised users on the new service you created, and add your dedicated sync user
Create a token to place in config.json
* Site administration -> Server -> Web services -> Manage tokens
* Create token, give descriptive name, user set to dedicated sync user, service is the descriptive name above, set appropriate valid until, click Save changes
* Copy this token shown on the next page and paste it in to config.json



## Config
Copy ```sampleconfig.json``` to ```instance/imisoauth2.json```

