# ------------------------------------------------------------------
# LOGO + LINKS TO OTHER SYSTEMS  -> paste at the BOTTOM of core_portal/settings.py
# ------------------------------------------------------------------

# Folder for your logo and icons. Create it next to manage.py:  static/icons/
STATICFILES_DIRS = [BASE_DIR / "static"]

# "Other systems" cards on the home page. One dictionary per system.
#   name         required
#   url          required, the full address including https://
#   description  optional, one short line
#   icon         optional, a file inside static/, for example "icons/nms.png".
#                Without it, a card shows the first letter of the name.
# Leave the list empty ([]) and the section is hidden.
PORTAL_LINKS = [
    # {
    #     "name": "Network Monitoring",
    #     "description": "Alarms and performance dashboards",
    #     "url": "https://nms.yourcompany.com",
    #     "icon": "icons/nms.png",
    # },
    # {
    #     "name": "Ticketing",
    #     "description": "Faults and change requests",
    #     "url": "https://tickets.yourcompany.com",
    # },
]
