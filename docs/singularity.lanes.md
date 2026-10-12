You have access to the SingularityAPI Reserve Lane booking API, which reserves shared inference lanes (DeepSeek V4.1 Flash, 262K context, 2 concurrent requests per lane) by the hour on my behalf.
The llms.txt file is a very good starting point. Read it first, then go from there based on what I need.

llms.txt (overview + all doc links): https://app.singularityapi.tech/llms.txt
llms-full.txt (complete reference with inline code examples): https://app.singularityapi.tech/llms-full.txt

Your agent token: ask me for it (it starts with sk-lane-). Send it as `Authorization: Bearer <token>`.

Ground rules:
- Only book what I ask for. Confirm the hours (in my time zone and UTC), lanes and total cost before booking unless I said to book without asking. If I say "start now", use start=now: the hour then runs exactly 60 minutes from the booking time (12:10 → 13:10), not from the top of the hour.
- Always send an idempotency_key per booking so a retry never double-books.
- Respect the token's daily spend cap and my credit balance (GET /api/v1/me); never try to work around them.
- After booking, report the reservation ids, the slot times and the cost. If a booking fails, tell me the error code and message verbatim.


## Quick Start
# List bookable hours, then reserve two consecutive hours on one lane
curl -s "https://app.singularityapi.tech/api/v1/slots?plan=flash-40" -H "Authorization: Bearer $LANE_TOKEN"

curl -s -X POST "https://app.singularityapi.tech/api/v1/reservations" \
  -H "Authorization: Bearer $LANE_TOKEN" -H "Content-Type: application/json" \
  -d '{"slot_ids":["<slot_id_1>","<slot_id_2>"],"lanes":1,"idempotency_key":"run-42"}'

# Balance, limits and today's spend on this token
curl -s "https://app.singularityapi.tech/api/v1/me" -H "Authorization: Bearer $LANE_TOKEN"

# Cancel (needs the cancel scope; allowed until one hour before the slot)
curl -s -X DELETE "https://app.singularityapi.tech/api/v1/reservations/<reservation_id>" -H "Authorization: Bearer $LANE_TOKEN"
