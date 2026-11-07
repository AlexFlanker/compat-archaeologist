# International Racking: legacy order-system export

A fictional company's 10-year-old MongoDB order system, exported as evidence for the archaeologist.
Fully synthetic and generated on 2026-11-07. No real company or customer data.

**Point the agent at this folder only.** Everything here is evidence it may read. Scoring material lives elsewhere and must never be given to the agent.

## Contents

| Path | What it is |
|---|---|
| `data/sales_leads.json` | `sales_leads` collection |
| `data/accounts.json` | `accounts` collection (business customers) |
| `data/contacts.json` | `contacts` collection (people at those customers) |
| `data/orders.json` | `orders` collection (quotes and orders) |
| `data/query_log.txt` | Query log: one query per line, `<UTC timestamp> <actor> <mongo shell query>` |
| `evidence/` | Code snippets recovered from the old system |

## Load into MongoDB

Each JSON file is an array of documents. Dates use MongoDB Extended JSON (`{"$date": ...}`), so load them with `mongoimport --jsonArray` or `bson.json_util.loads`. A plain `json.load` turns them into dicts instead of dates.

```bash
for c in sales_leads accounts contacts orders; do
  mongoimport --uri "$MONGODB_URI" --db intl_racking --collection "$c" \
    --jsonArray --file "datasets/international_racking/data/$c.json"
done
```
