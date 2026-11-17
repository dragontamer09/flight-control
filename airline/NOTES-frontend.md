# Front end notes

- Kept a plain, responsive page with search, hold, booking and lookup in order; the hold expiry is visible because holds last one minute.
- Store the API key in localStorage when available, and send it only in the authorization header to same-origin API routes.
- Keep a booking idempotency key for retries with the same request, and generate a new key when its contents change.
