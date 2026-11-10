LUCKY DRAW - TALLY CONNECTOR
============================
Put this folder on the PC where Tally is open. Nothing needs to be installed.

1. In TallyPrime: F1 (Help) > Settings > Connectivity > Client/Server configuration.
   Set "TallyPrime acts as" to Both, "Enable ODBC" to Yes, port 9000. Let Tally restart and load the company.
2. Double-click connector.bat. A black window opens and stays open. That is normal.
3. In the lucky-draw website, open "Tally setup": it shows "Connector is running" within a few seconds.
4. To start the connector automatically at login, double-click install_autostart.bat once.

connector.ini holds the website address and a secret key. Do not share it or post it anywhere.
The connector only READS from Tally. It never changes anything in Tally.
If Tally uses a port other than 9000, change the tally= line in connector.ini.
