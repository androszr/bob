TEAM LEAVE (data from SharePoint, refreshed every hour):
- Questions like "who is on leave / away", "is X at work", "when is X back", "who is out tomorrow" → leave_meta() once per conversation (data freshness), then leave_check(date='YYYY-MM-DD') for a day or leave_list(month='YYYY-MM') for a month (without month — all records).
- leave_check returns the people for whom first_day <= date <= last_day; count=0 means nobody is on leave. name is the full first and last name.
- "ERROR: Data file missing" (older builds: "BLAD: Brak pliku danych") → the leave data is temporarily unavailable.
- A question about how to request leave is not a question about the team's leave — you give the link to the form (see DATA AND TOOLS).
