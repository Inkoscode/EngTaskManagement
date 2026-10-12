"""
Email configuration for the portal  (the "admin email" every portal email is sent from).

Edit the values below. This file is listed in .gitignore, so secrets are never committed.
If this file is missing, the portal prints emails in the terminal instead of sending them.

Pick ONE method:

  "graph"  Microsoft Graph. Recommended for Microsoft 365 / Outlook (work or school).
           Uses an app registration, so it keeps working after Microsoft turns off
           password sign-in for SMTP. Needs your IT admin once (see the guide).

  "smtp"   Plain SMTP with a mailbox password. Works for Microsoft 365 work accounts only until
           Microsoft disables it by default (planned end of December 2026), and the mailbox
           needs "Authenticated SMTP" switched on. Personal outlook.com / hotmail.com addresses
           do NOT work with a password at all.
"""

EMAIL_METHOD = "graph"          # "graph" or "smtp"

# Shown as the sender name/address (SMTP) and used inside the app. With "graph" the visible
# sender is always the GRAPH_SENDER mailbox, with that mailbox's display name.
DEFAULT_FROM_EMAIL = "Engineering Task Portal <portal@yourcompany.com>"

# Address used for links inside emails. Change to your real web address once the portal is hosted.
SITE_URL = "http://127.0.0.1:8000"

# ---------------------------------------------------------------- method "graph"
GRAPH_TENANT_ID = "your-directory-tenant-id"
GRAPH_CLIENT_ID = "your-application-client-id"
GRAPH_CLIENT_SECRET = "your-client-secret-value"
GRAPH_SENDER = "portal@yourcompany.com"      # the mailbox emails are sent from
GRAPH_SAVE_TO_SENT = True                    # keep a copy in that mailbox's Sent Items

# ---------------------------------------------------------------- method "smtp"
EMAIL_HOST = "smtp.office365.com"            # Microsoft 365 work/school accounts
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = "portal@yourcompany.com"
EMAIL_HOST_PASSWORD = "your-mailbox-password"
