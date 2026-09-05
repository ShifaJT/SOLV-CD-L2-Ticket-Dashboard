SOLV L2 Dashboard v15

TAT source

The app reads the current Google Sheet at runtime:

Spreadsheet ID: 1WONn8c8JQjmVYYYpt06jA-DUzH7YkroE4IxHoMO-TqM

Tab: gid=0

Required columns: Subcategory, SLA effective Jun'25

The app does not use a hard-coded 48-hour SLA and does not fall back to the old partial mapping.

If the Google Sheet is publicly readable

No extra setup is needed. The app uses the Google Sheets CSV export.

If the Google Sheet is private

Add a Streamlit secret named GOOGLE_SERVICE_ACCOUNT_JSON containing the service-account JSON object, and share the Google Sheet with the service-account email as Viewer.

L2 groups

The main L2 population is exactly:

CD L2

Ops - L2

CD - Seller Support

Tech Support

Logistics

TAT logic

Closed/resolved + mapped TAT + resolution <= TAT: Closed Within TAT

Closed/resolved + mapped TAT + resolution > TAT: Closed Beyond TAT

Open/pending + mapped TAT + current age <= TAT: Open Within TAT

Open/pending + mapped TAT + current age > TAT: Open Beyond TAT

No numeric TAT: TAT Not Mapped

TAT compliance is:
Closed Within TAT / (Closed Within TAT + Closed Beyond TAT)

Exact group view

The non-L2 current-group sections use the exact Group values from the raw dump, rather than a generic "Other Groups" label.

Important handoff limitation

The raw ticket dump contains current Group, not historical group-transfer history. Therefore L1 → L2 is shown only as a CreatedBy proxy and is not presen
