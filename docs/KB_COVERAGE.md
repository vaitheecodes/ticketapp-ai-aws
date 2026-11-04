# Knowledge-base coverage map

The live KB (`data/kb/kb_entries.json`) has 386 entries, but only 27 distinct
topics: one answer per intent, repeated with different wording. The 27K-row
source CSV holds the same 386 questions and 54 answers, so it adds nothing and
is not imported. This map lists the Site24x7 topics the KB doesn't cover yet.

Every topic below has a **draft** in `data/kb/new_entries.json` (same id). The
app ignores drafts. A person reviews each one, writes or corrects the answer,
and changes `status` to `approved`. `scripts/validate_kb.py` reports the drafts
by area.

- **Questions** are wording variants written for retrieval. They aren't quotes
  from customers.
- **Collect** lists what the AI can usefully ask for. It never asks for account
  identifiers.
- **Handling** is either `answer` (the KB answer resolves the question) or
  `escalate_with_info`. The second means it needs someone with account access,
  so the AI gathers the listed details and hands the chat over.
- **Draft answer** is a short paraphrase of the official page in `source_url`.
  Empty means it is still to be written. No answer was written from memory.

Sources are Site24x7's official help site (`site24x7.com/help`) and its official
support knowledge base (`support.site24x7.com`). Entries from the support site
are marked (support KB) so you can decide whether to accept them.

**Category caveat:** the classifier's 17 categories have no database,
virtualization or status-page category. Drafts use the closest one:
APPLICATION_MONITORING for databases, SERVER_MONITORING for virtualization and
credentials, and REPORTING for status pages. Review these when you approve.

**Topics added beyond the starting list:**

- domain expiry
- PostgreSQL, as the "other databases" entry
- monitoring-location IP allowlisting, separate from the agent's firewall rules
- proxy split into Linux and Windows
- WMI split from SSH credentials
- add-monitor errors split by monitor type

## Web and internet services

### SSL/TLS certificate (KB-N0101, `WEB_MONITORING` / `ssl_certificate_monitoring`)

- **Handling:** `answer`
- **Questions:** "How do I monitor SSL certificate expiry?" · "I did not get an alert before my SSL certificate expired" · "SSL monitor says the certificate is not trusted"
- **Collect:** Host and port being checked; Exact status or error shown on the monitor; Whether the certificate is self-signed or uses intermediate certificates; Expiry threshold set (days)
- **Draft answer:** yes, from [/help/admin/adding-a-monitor/ssl-certificate-monitor.html](https://www.site24x7.com/help/admin/adding-a-monitor/ssl-certificate-monitor.html)

### Domain expiry (KB-N0102, `WEB_MONITORING` / `domain_expiry_monitoring`)

- **Handling:** `answer`
- **Questions:** "How do I get alerted before my domain expires?" · "domain expiry monitor setup" · "domain expiry monitor shows the wrong expiry date"
- **Collect:** Domain being monitored; Expiry date shown versus the registrar's date; WHOIS server, if set
- **Draft answer:** yes, from [/help/admin/adding-a-monitor/domain-expiry-monitor.html](https://www.site24x7.com/help/admin/adding-a-monitor/domain-expiry-monitor.html)

### Ping (KB-N0103, `MONITORING` / `ping_monitoring`)

- **Handling:** `answer`
- **Questions:** "My ping monitor shows down but the server is up" · "How do I add a ping monitor?" · "ping monitor keeps timing out from some locations"
- **Collect:** Host or IP; Locations reporting down; Whether ICMP is allowed by the host and firewalls; IPv4 or IPv6
- **Draft answer:** yes, from [/help/admin/adding-a-monitor/ping-monitor.html](https://www.site24x7.com/help/admin/adding-a-monitor/ping-monitor.html)

### Port (custom protocol) (KB-N0104, `MONITORING` / `port_monitoring`)

- **Handling:** `answer`
- **Questions:** "How do I monitor whether a TCP port is open?" · "port monitor shows down but the service is running" · "alert me when a port is closed"
- **Collect:** Host and port; Locations reporting down; Port status check setting (alert on closed or open); Any keyword checks configured
- **Draft answer:** yes, from [/help/admin/adding-a-monitor/port-monitor.html](https://www.site24x7.com/help/admin/adding-a-monitor/port-monitor.html)

### DNS (KB-N0105, `MONITORING` / `dns_monitoring`)

- **Handling:** `answer`
- **Questions:** "How do I monitor DNS resolution for my domain?" · "DNS monitor shows answer mismatch" · "DNS monitor connection error"
- **Collect:** DNS server, domain and record type checked; Exact error on the monitor; Expected values configured for answer validation
- **Draft answer:** yes, from [/help/admin/adding-a-monitor/dns-monitor.html](https://www.site24x7.com/help/admin/adding-a-monitor/dns-monitor.html)

## Digital experience

### Web transaction (browser) (KB-N0106, `WEB_MONITORING` / `web_transaction_monitoring`)

- **Handling:** `answer`
- **Questions:** "How do I monitor a login or checkout flow?" · "web transaction monitor fails at one step" · "how do I record a synthetic transaction?"
- **Collect:** Monitor name and the step that fails; Error shown for that step; Whether the page changed recently; Location or On-Premise Poller used
- **Draft answer:** yes, from [/help/admin/adding-a-monitor/webapplication-monitoring-realbrowser.html](https://www.site24x7.com/help/admin/adding-a-monitor/webapplication-monitoring-realbrowser.html)

### Real user monitoring (RUM) (KB-N0107, `APM` / `real_user_monitoring`)

- **Handling:** `answer`
- **Questions:** "How do I set up real user monitoring?" · "RUM shows no data after adding the script" · "where do I put the RUM snippet?"
- **Collect:** Website URL; Where the snippet was placed (header or footer); Browser console errors, if any; When the snippet was added
- **Draft answer:** yes, from [/help/apm/rum.html](https://www.site24x7.com/help/apm/rum.html)

### Status page (StatusIQ) (KB-N0108, `REPORTING` / `status_page`)

- **Handling:** `answer`
- **Questions:** "How do I create a public status page?" · "status page setup" · "how do I show incidents on a status page?"
- **Collect:** Status page name or URL; What should appear on it; Whether it should be public or private
- **Draft answer:** yes, from [/help/statuspage/setup-status-page/status-page.html](https://www.site24x7.com/help/statuspage/setup-status-page/status-page.html)

## Network access and connectivity

### Server agent (KB-N0109, `AGENT` / `agent_firewall_ports`)

- **Handling:** `answer`
- **Questions:** "Which ports and domains does the agent need through our firewall?" · "agent cannot connect through firewall" · "what do I allowlist for the server agent?"
- **Collect:** Operating system and agent version; Data center or region of the account; Firewall or proxy in use; Agent log error, if any
- **Draft answer:** yes, from [/portal/en/kb/articles/which-ports-do-i-need-to-allow-access-in-my-firewall-to-use-site24x7-agent](https://support.site24x7.com/portal/en/kb/articles/which-ports-do-i-need-to-allow-access-in-my-firewall-to-use-site24x7-agent) (support KB)

### Monitoring locations (KB-N0110, `MONITORING` / `monitoring_ip_allowlisting`)

- **Handling:** `answer`
- **Questions:** "Which Site24x7 IPs should I allowlist?" · "our firewall blocks Site24x7 checks" · "website monitor down because of IP blocking"
- **Collect:** Which monitor and locations are affected; Firewall or WAF in front of the site; Whether allowlisting is by IP or by domain
- **Draft answer:** yes, from [/portal/en/kb/articles/automating-your-ip-whitelisting](https://support.site24x7.com/portal/en/kb/articles/automating-your-ip-whitelisting) (support KB)

### Server agent (Linux) (KB-N0111, `AGENT` / `agent_proxy_linux`)

- **Handling:** `answer`
- **Questions:** "How do I set a proxy for the Linux agent?" · "Linux agent behind a proxy is not reporting" · "change proxy settings on an installed Linux agent"
- **Collect:** Agent install path (root or non-root); Proxy host and port, and whether it needs authentication; Agent log error, if any
- **Draft answer:** yes, from [/portal/en/kb/articles/how-do-i-configure-proxy-for-already-installed-site24x7-agent](https://support.site24x7.com/portal/en/kb/articles/how-do-i-configure-proxy-for-already-installed-site24x7-agent) (support KB)

### Server agent (Windows) (KB-N0112, `AGENT` / `agent_proxy_windows`)

- **Handling:** `answer`
- **Questions:** "How do I set a proxy for the Windows agent?" · "Windows agent not reporting behind a proxy" · "where are the proxy settings for the Windows agent?"
- **Collect:** Agent version; Proxy host and port, and whether it needs authentication; Agent log error, if any
- **Draft answer:** yes, from [/portal/en/kb/articles/how-do-i-set-the-porxy-for-my-windows-agent](https://support.site24x7.com/portal/en/kb/articles/how-do-i-set-the-porxy-for-my-windows-agent) (support KB)

### On-Premise Poller (KB-N0113, `MONITORING` / `on_premise_poller`)

- **Handling:** `answer`
- **Questions:** "How do I monitor servers inside my private network?" · "On-Premise Poller is not sending data" · "what is the On-Premise Poller for?"
- **Collect:** Poller OS, version and 32/64-bit; Poller status in the console; Firewall or proxy between the poller and Site24x7; Poller log error, if any
- **Draft answer:** yes, from [/help/getting-started/on-premise-poller.html](https://www.site24x7.com/help/getting-started/on-premise-poller.html)

## Databases

### MySQL / MariaDB (KB-N0114, `APPLICATION_MONITORING` / `mysql_monitoring`)

- **Handling:** `answer`
- **Questions:** "How do I monitor MySQL?" · "MySQL monitor shows no data" · "what privileges does the MySQL monitoring user need?"
- **Collect:** MySQL or MariaDB version; Agent version; Exact error from the add-instance step or the monitor
- **Draft answer:** yes, from [/help/database-monitoring/mysql-monitor.html](https://www.site24x7.com/help/database-monitoring/mysql-monitor.html)

### Microsoft SQL Server (KB-N0115, `APPLICATION_MONITORING` / `sql_server_monitoring`)

- **Handling:** `answer`
- **Questions:** "How do I monitor SQL Server?" · "SQL Server monitor missing after installing the agent" · "does SQL Server monitoring need a login?"
- **Collect:** SQL Server version; Windows agent version; Whether the instance appears under the server monitor
- **Draft answer:** yes, from [/help/admin/adding-a-monitor/sql-server-monitor.html](https://www.site24x7.com/help/admin/adding-a-monitor/sql-server-monitor.html)

### Oracle Database (KB-N0116, `APPLICATION_MONITORING` / `oracle_monitoring`)

- **Handling:** `answer`
- **Questions:** "How do I monitor an Oracle database?" · "Oracle monitor add instance fails" · "which privileges does the Oracle monitoring user need?"
- **Collect:** Oracle version and whether it is multi-tenant; Agent version; Exact error from the add-instance step
- **Draft answer:** yes, from [/help/database-monitoring/oracle-monitor.html](https://www.site24x7.com/help/database-monitoring/oracle-monitor.html)

### PostgreSQL (KB-N0117, `APPLICATION_MONITORING` / `postgresql_monitoring`)

- **Handling:** `answer`
- **Questions:** "How do I monitor PostgreSQL?" · "PostgreSQL monitor shows no data" · "which roles does the PostgreSQL monitoring user need?"
- **Collect:** PostgreSQL version; Agent version; Exact error from the add-instance step
- **Draft answer:** yes, from [/help/database-monitoring/postgresql-monitor.html](https://www.site24x7.com/help/database-monitoring/postgresql-monitor.html)

## Virtualization and server credentials

### VMware ESX/ESXi / vCenter (KB-N0118, `SERVER_MONITORING` / `vmware_monitoring`)

- **Handling:** `answer`
- **Questions:** "How do I monitor VMware ESXi?" · "VMware monitor cannot connect to vCenter" · "what permissions does the VMware account need?"
- **Collect:** vCenter or ESXi host and port; On-Premise Poller version; Role of the VMware account; Exact error when adding the monitor
- **Draft answer:** yes, from [/help/admin/adding-a-monitor/vmware-esx-esxi-monitor.html](https://www.site24x7.com/help/admin/adding-a-monitor/vmware-esx-esxi-monitor.html)

### Hyper-V (KB-N0119, `SERVER_MONITORING` / `hyperv_monitoring`)

- **Handling:** `answer`
- **Questions:** "How do I monitor Hyper-V?" · "Hyper-V VMs show only partial details" · "Hyper-V not discovered"
- **Collect:** Windows Server version of the host; Windows agent version; What is missing in the console
- **Draft answer:** yes, from [/help/server-metrics/hyper-v-monitoring.html](https://www.site24x7.com/help/server-metrics/hyper-v-monitoring.html)

### Credential profile (SSH) (KB-N0120, `SERVER_MONITORING` / `ssh_credentials`)

- **Handling:** `answer`
- **Questions:** "How do I store SSH credentials for monitoring?" · "use an SSH private key for a monitor" · "update a password used by several monitors"
- **Collect:** Which monitors use the credentials; Credential type (password, private key, certificate); Exact error when testing or polling
- **Draft answer:** yes, from [/help/admin/configuration-profiles/credential-profile.html](https://www.site24x7.com/help/admin/configuration-profiles/credential-profile.html)

### Agentless Windows (WMI) (KB-N0121, `SERVER_MONITORING` / `wmi_credentials`)

- **Handling:** `escalate_with_info`
- **Questions:** "WMI credentials fail for agentless Windows monitoring" · "which account do I use for WMI monitoring?" · "agentless Windows server shows access denied"
- **Collect:** Whether the server is in a domain or a workgroup; Account type used (domain user, local user); On-Premise Poller version; Exact error text
- **Draft answer:** empty (to be written)

## Cloud

### AWS (KB-N0122, `CLOUD` / `aws_monitoring`)

- **Handling:** `answer`
- **Questions:** "How do I connect my AWS account?" · "AWS integration not discovering resources" · "do I need access keys for AWS monitoring?"
- **Collect:** Connection method used (IAM role / CloudFormation); AWS services expected; Exact error shown
- **Draft answer:** yes, from [/help/aws/deployment-methods.html](https://www.site24x7.com/help/aws/deployment-methods.html)

### Google Cloud (KB-N0123, `CLOUD` / `gcp_monitoring`)

- **Handling:** `answer`
- **Questions:** "How do I connect Google Cloud?" · "GCP monitor shows permission error" · "which roles does the GCP service account need?"
- **Collect:** Project ID; Roles granted to the service account; Exact error shown
- **Draft answer:** yes, from [/help/gcp/getting-started.html](https://www.site24x7.com/help/gcp/getting-started.html)

## Alerts and integrations

### Email alerts (KB-N0124, `ALERTS` / `alert_email_delivery`)

- **Handling:** `answer`
- **Questions:** "I am not receiving alert emails" · "Site24x7 alert emails go to spam" · "alert emails stopped arriving"
- **Collect:** Recipient address; When the last alert email arrived; Whether other users receive them; Mail filtering in use
- **Draft answer:** yes, from [/portal/en/kb/articles/i-am-not-receiving-alert-emails-from-site24x7-what-should-i-do](https://support.site24x7.com/portal/en/kb/articles/i-am-not-receiving-alert-emails-from-site24x7-what-should-i-do) (support KB)

### SMS alerts (KB-N0125, `ALERTS` / `alert_sms_delivery`)

- **Handling:** `answer`
- **Questions:** "I am not receiving SMS alerts" · "SMS alerts stopped" · "added my mobile number but get no SMS"
- **Collect:** Mobile number as entered (country code); User alert group the user belongs to; Whether the number is on a Do Not Disturb list
- **Draft answer:** yes, from [/portal/en/kb/articles/i-ve-added-mobile-number-to-my-account-but-i-m-not-receiving-sms-alerts-what-could-be-the-reason](https://support.site24x7.com/portal/en/kb/articles/i-ve-added-mobile-number-to-my-account-but-i-m-not-receiving-sms-alerts-what-could-be-the-reason) (support KB)

### Webhook (KB-N0126, `INTEGRATION` / `webhook_integration`)

- **Handling:** `answer`
- **Questions:** "How do I send alerts to a webhook?" · "webhook integration not receiving alerts" · "can a webhook call an internal URL?"
- **Collect:** Hook URL host (no secrets); HTTP method and body format; Result of Save and Test; Whether the endpoint is internal
- **Draft answer:** yes, from [/help/admin/third-party-integration/webhooks.html](https://www.site24x7.com/help/admin/third-party-integration/webhooks.html)

### Microsoft Teams (KB-N0127, `INTEGRATION` / `teams_integration`)

- **Handling:** `answer`
- **Questions:** "How do I send alerts to Microsoft Teams?" · "Teams integration stopped posting" · "Teams connector deprecated"
- **Collect:** Channel used; Result of the test alert; Whether the old connector-based setup was used
- **Draft answer:** yes, from [/help/admin/third-party-integration/microsoft-teams.html](https://www.site24x7.com/help/admin/third-party-integration/microsoft-teams.html)

### Slack (KB-N0128, `INTEGRATION` / `slack_integration`)

- **Handling:** `answer`
- **Questions:** "How do I send alerts to Slack?" · "Slack integration not posting alerts" · "test Slack alerts"
- **Collect:** Connection type (webhook URL or OAuth token); Channel; Result of Save and Test
- **Draft answer:** yes, from [/help/admin/third-party-integration/slack.html](https://www.site24x7.com/help/admin/third-party-integration/slack.html)

### Scheduled maintenance (KB-N0129, `ALERTS` / `maintenance_window`)

- **Handling:** `answer`
- **Questions:** "How do I stop alerts during maintenance?" · "schedule a recurring maintenance window" · "got alerts during a maintenance window"
- **Collect:** Window schedule and time zone; Monitors or groups included; Time of the unexpected alert
- **Draft answer:** yes, from [/help/admin/operations/scheduled-maintenance.html](https://www.site24x7.com/help/admin/operations/scheduled-maintenance.html)

## Account and access

### User roles (KB-N0130, `ACCOUNT` / `user_roles`)

- **Handling:** `answer`
- **Questions:** "What can each user role do?" · "change a user's role" · "read only user cannot edit monitors"
- **Collect:** Current role of the user; What they need to do; Whether they accepted the invitation
- **Draft answer:** yes, from [/help/admin/user-management/role-based-access-permissions.html](https://www.site24x7.com/help/admin/user-management/role-based-access-permissions.html)

### Account security (2FA/MFA) (KB-N0131, `ACCOUNT` / `two_factor_authentication`)

- **Handling:** `answer`
- **Questions:** "How do I enable two-factor authentication?" · "turn on MFA for my organization" · "2FA for Site24x7 login"
- **Collect:** Whether it is for one user or the whole organization; Role of the person enabling it
- **Draft answer:** yes, from [/portal/en/kb/articles/how-do-i-secure-my-site24x7-account-using-two-factor-authentication](https://support.site24x7.com/portal/en/kb/articles/how-do-i-secure-my-site24x7-account-using-two-factor-authentication) (support KB)

### Single sign-on (SAML) (KB-N0132, `ACCOUNT` / `saml_sso`)

- **Handling:** `answer`
- **Questions:** "How do I set up SSO with SAML?" · "SAML login not working" · "can I use Okta or Azure AD to sign in?"
- **Collect:** Identity provider used; Whether the person is the Super Admin; Exact error at sign-in
- **Draft answer:** yes, from [/portal/en/kb/articles/saml-based-sso](https://support.site24x7.com/portal/en/kb/articles/saml-based-sso) (support KB)

### MSP (KB-N0133, `ACCOUNT` / `msp_customer_account`)

- **Handling:** `answer`
- **Questions:** "How do I add a customer to my MSP account?" · "MSP customer did not get the invitation" · "MSP user cannot see a customer account"
- **Collect:** MSP account and customer name; Whether the customer verified their mobile number; Which MSP users are associated with the customer
- **Draft answer:** yes, from [/help/msp/customer-management/customer-account.html](https://www.site24x7.com/help/msp/customer-management/customer-account.html)

## AI assistant

### Zia (KB-N0134, `INTEGRATION` / `zia_assistant`)

- **Handling:** `answer`
- **Questions:** "What is Zia in Site24x7?" · "how do I enable Zia?" · "Zia not available in my account"
- **Collect:** Which integration method is set up (BYOK, ZKS, Azure OpenAI); Role of the user; What Zia shows
- **Draft answer:** yes, from [/help/admin/third-party-integration/zia-integration.html](https://www.site24x7.com/help/admin/third-party-integration/zia-integration.html)

## Add-monitor errors

### Website (KB-N0135, `WEB_MONITORING` / `add_website_monitor_error`)

- **Handling:** `escalate_with_info`
- **Questions:** "I get an error when adding a website monitor" · "website monitor will not save" · "add monitor fails for my URL"
- **Collect:** The URL being added (no credentials); Exact error text; Screenshot of the form; Whether the site needs a login
- **Draft answer:** empty (to be written)

### Server agent (KB-N0136, `AGENT` / `add_server_agent_error`)

- **Handling:** `escalate_with_info`
- **Questions:** "The agent installs but the server never appears" · "server agent registration fails" · "error while installing the Site24x7 agent"
- **Collect:** Operating system and version; Agent version and install method; Exact error text or agent log lines; Proxy or firewall in use
- **Draft answer:** empty (to be written)

### Network device (SNMP) (KB-N0137, `SNMP` / `add_network_device_error`)

- **Handling:** `escalate_with_info`
- **Questions:** "Network device cannot be added" · "SNMP device discovery fails" · "error adding a router or switch"
- **Collect:** Device vendor and model; SNMP version and whether credentials were tested; On-Premise Poller version; Exact error text
- **Draft answer:** empty (to be written)

### Cloud (AWS/Azure/GCP) (KB-N0138, `CLOUD` / `add_cloud_account_error`)

- **Handling:** `escalate_with_info`
- **Questions:** "Error when connecting my cloud account" · "cloud integration fails with permission error" · "cloud resources not discovered after connecting"
- **Collect:** Cloud provider and connection method; Exact error text; Roles or policies granted
- **Draft answer:** empty (to be written)

### Database (KB-N0139, `APPLICATION_MONITORING` / `add_database_monitor_error`)

- **Handling:** `escalate_with_info`
- **Questions:** "Error when adding a database monitor" · "database instance add fails" · "database monitor shows no data"
- **Collect:** Database type and version; Agent version; Exact error from the add-instance step or the monitor
- **Draft answer:** empty (to be written)

### REST API (KB-N0140, `WEB_MONITORING` / `add_api_monitor_error_har`)

- **Handling:** `escalate_with_info`
- **Questions:** "Error when adding a REST API monitor" · "API monitor will not save" · "invalid data provided error for an API monitor"
- **Collect:** Exact error text; Endpoint host and method (no tokens); A HAR file of the failing add-monitor attempt, with cookies and tokens removed
- **Draft answer:** yes, from [/portal/en/kb/articles/how-to-generate-a-har-file](https://support.site24x7.com/portal/en/kb/articles/how-to-generate-a-har-file) (support KB)

## Getting started (how-to)

Setup tasks the canonical KB has no steps for: on the live demo, "how to add a monitor in the monitoring agent" found no entry, and "how to generate a report" matched an entry with no steps. Each entry has `kind: "how-to"` and numbered steps taken only from the page in `source_url`.

### Linux server agent (KB-N0141, `AGENT` / `agent_installation`)

- **Handling:** `answer`, kind `how-to`
- **Questions:** "How do I install the Site24x7 agent on a Linux server?" · "how to add a linux server monitor" · "set up monitoring for my linux box with the agent" · "add a monitor in the monitoring agent on linux"
- **Collect:** Linux distribution and version; Whether the agent must run as root or non-root; Exact error shown by the install command, if any
- **Draft answer:** yes, from [/help/admin/adding-a-monitor/linux-server-monitoring.html](https://www.site24x7.com/help/admin/adding-a-monitor/linux-server-monitoring.html)

### Windows server agent (KB-N0142, `AGENT` / `agent_installation`)

- **Handling:** `answer`, kind `how-to`
- **Questions:** "How do I install the Site24x7 agent on a Windows server?" · "how to add a windows server monitor" · "set up windows server monitoring with the agent" · "where do I download the windows agent"
- **Collect:** Windows Server version; Whether a proxy is needed; Exact error shown by the installer, if any
- **Draft answer:** yes, from [/help/admin/adding-a-monitor/windows-server-monitoring.html](https://www.site24x7.com/help/admin/adding-a-monitor/windows-server-monitoring.html)

### Website (KB-N0143, `WEB_MONITORING` / `website_monitoring`)

- **Handling:** `answer`, kind `how-to`
- **Questions:** "How do I add a website monitor?" · "how to monitor my website uptime" · "add a URL to monitor" · "set up a website check"
- **Collect:** The URL to monitor; Check frequency wanted; Error shown by Check and Save, if any
- **Draft answer:** yes, from [/help/admin/adding-a-monitor/website-monitoring.html](https://www.site24x7.com/help/admin/adding-a-monitor/website-monitoring.html)

### Custom report (KB-N0144, `REPORTING` / `report_dashboard`)

- **Handling:** `answer`, kind `how-to`
- **Questions:** "How do I generate a report?" · "how to create a custom report" · "make a report of my monitors' metrics" · "how do I export a report as CSV"
- **Collect:** What the report should show (monitors and metrics); Time period
- **Draft answer:** yes, from [/help/reports/custom-report.html](https://www.site24x7.com/help/reports/custom-report.html)

### Scheduled report (KB-N0145, `REPORTING` / `report_dashboard`)

- **Handling:** `answer`, kind `how-to`
- **Questions:** "How do I schedule a report to be emailed?" · "how to send a weekly report automatically" · "set up a scheduled report in PDF" · "email reports every month"
- **Collect:** Report type; Format (CSV or PDF); How often it should be sent
- **Draft answer:** yes, from [/help/admin/reports-settings/scheduled-reports.html](https://www.site24x7.com/help/admin/reports-settings/scheduled-reports.html)

### Users (KB-N0146, `ACCOUNT` / `user_management`)

- **Handling:** `answer`, kind `how-to`
- **Questions:** "How do I add a user to my account?" · "how to invite a team member" · "add a new user and give them a role" · "create a login for a colleague"
- **Collect:** Role the new user needs; Which monitors they should see; Whether the user is outside the organization
- **Draft answer:** yes, from [/help/admin/user-management/users.html](https://www.site24x7.com/help/admin/user-management/users.html)

### Notification profile (KB-N0147, `ALERTS` / `alert_configuration`)

- **Handling:** `answer`, kind `how-to`
- **Questions:** "How do I configure alerts and notifications?" · "how to set up a notification profile" · "choose how and when alerts are sent" · "set an alert delay or escalation"
- **Collect:** Which statuses should alert; Notification mediums wanted; Delay or escalation wanted
- **Draft answer:** yes, from [/help/admin/configuration-profiles/notification-profile.html](https://www.site24x7.com/help/admin/configuration-profiles/notification-profile.html)

### User alert group (KB-N0148, `ALERTS` / `alert_configuration`)

- **Handling:** `answer`, kind `how-to`
- **Questions:** "How do I choose who receives alerts?" · "how to create a user alert group" · "send alerts to my team" · "add people to an alert group"
- **Collect:** Who should receive the alerts; Which monitors they are for
- **Draft answer:** yes, from [/help/admin/user-management/user-groups.html](https://www.site24x7.com/help/admin/user-management/user-groups.html)

### Monitor group (KB-N0149, `MONITORING` / `monitor_groups`)

- **Handling:** `answer`, kind `how-to`
- **Questions:** "How do I create a monitor group?" · "how to group my monitors" · "add monitors to a group" · "organize monitors by application"
- **Collect:** Which monitors go in the group; Whether subgroups or a dependency are needed
- **Draft answer:** yes, from [/help/admin/monitor-groups.html](https://www.site24x7.com/help/admin/monitor-groups.html)

### iOS app (KB-N0150, `APM` / `mobile_apm`)

- **Handling:** `answer`, kind `how-to`
- **Questions:** "How do I add my iOS app for monitoring?" · "how to monitor an iPhone app" · "set up mobile APM for iOS" · "add an IOS app"
- **Collect:** App name; Whether the project uses CocoaPods; Error shown by pod install or the build, if any
- **Draft answer:** yes, from [/help/apm/ios-app-optimization.html](https://www.site24x7.com/help/apm/ios-app-optimization.html)

### Android app (KB-N0151, `APM` / `mobile_apm`)

- **Handling:** `answer`, kind `how-to`
- **Questions:** "How do I add my Android app for monitoring?" · "how to monitor an android app" · "set up mobile APM for Android" · "add an android application"
- **Collect:** App name; Error shown by the Gradle build, if any
- **Draft answer:** yes, from [/help/apm/android-app-optimization.html](https://www.site24x7.com/help/apm/android-app-optimization.html)

### APM Insight .NET agent (KB-N0152, `APM` / `apm_setup`)

- **Handling:** `answer`, kind `how-to`
- **Questions:** "How do I add an APM monitor?" · "how to set up APM Insight for my .NET application" · "install the APM agent on my IIS server" · "monitor my ASP.NET app performance"
- **Collect:** Windows and IIS version; Error shown by the installer, if any
- **Draft answer:** yes, from [/help/admin/adding-a-monitor/apm-insight-using-dotnet-agent.html](https://www.site24x7.com/help/admin/adding-a-monitor/apm-insight-using-dotnet-agent.html)

## Not drafted yet

These are obvious gaps that have no draft. They're good candidates for the next round:

- Azure-specific connection errors (Azure exists in the KB only as a general topic).
- SMTP, POP and mail delivery monitors.
- Monitor dependencies (stopping duplicate alerts when a parent is down); creating a monitor group is drafted (KB-N0149).
- SLA definitions (custom and scheduled reports are drafted: KB-N0144, KB-N0145).
- The mobile app and push notifications.
