# Compat Archaeologist

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3) ![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

Before you migrate a database off MongoDB, get the archaeology done.

Ask me to **assess a migration**. I run a real pipeline on the application:

1. I rebuild the application's actual MongoDB usage spec from three kinds of evidence: data samples, the query log and the code. Each item is marked OBSERVED or INFERRED, with a confidence.
2. I run behavioral probes and replay the recorded workload on MongoDB and on FerretDB, side by side. AWS DocumentDB is compared against its public documentation and labeled INFERRED.
3. I price it over 12 months and answer with a decision, shown as interactive cards: what breaks, the ledger, latency and query plans.

The same pipeline tells a simple CRUD app that migrating is fine. Try "assess the control app".

Example prompts:
- "Assess the migration of ShopLog from MongoDB to a compatible engine"
- "What breaks?"
- "Show the ledger"
- "Assess the control app"

The application assessed here, ShopLog, is fictional and its data is synthetic.

Code: https://github.com/AlexFlanker/compat-archaeologist
