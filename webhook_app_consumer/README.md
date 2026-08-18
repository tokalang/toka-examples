# Webhook Application Consumer

A fresh, isolated black-box application consumer that resolves, locks, builds, and executes the released `webhook` (v0.1.1) application package on top of Toka v1.0.0-rc.6.

## Verification

To run both online resolution and offline replay tests:

```bash
TOKA_SDK=/path/to/extracted-rc6-sdk python3 verify_consumer.py
```
