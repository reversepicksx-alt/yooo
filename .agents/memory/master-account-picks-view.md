---
name: Master account picks view
description: The owner account sees all picks from every user for total calibration visibility
---

# Master account picks view

## Problem

Calibration data was spread across every user account. The owner account only saw its own picks, so there was no single place to view all settled data for system-wide calibration and auditing.

## Fix

`backend/routes/picks.py` `list_picks` now returns every pick in the `picks` collection when the requesting email matches `OWNER_EMAIL`. Other users continue to see only their own picks. The endpoint caps the master view at 5000 most-recent picks to keep mobile performance reasonable.

## Why

Total calibration signal comes from one place. The owner can now see every saved/settled pick across all users, spot problem leagues, and verify the calibration system is working.

## How to apply

- Any future owner-only reporting should gate on `req.email.lower() == OWNER_EMAIL` rather than a separate collection or copy.
- If the pick volume grows beyond 5000, add server-side pagination for the master view instead of raising the cap.
