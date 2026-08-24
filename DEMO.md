# 3–5 minute demo runbook

1. Start the API with `uvicorn app.main:app --reload` and open `http://127.0.0.1:8000/docs`.
2. **Happy path:** send `I want to return TR-4530` as `C-101`, then send `confirm` with the same session. Show the returned `return_created` action/reference.
3. **Edge case 1:** send `What happened to TR-4526? I need a refund.` as `C-101`. Show the lost-parcel acknowledgement, human escalation action, and factual handoff summary.
4. **Edge case 2:** send `Can I return TR-4527?` as `C-102` with `as_of: 2026-08-01`. Show the non-returnable jewellery refusal. Optionally show `TR-4528` as final-sale / size-exchange only.
5. **Safety:** ask `My COD bank account is 1234567890123456`. Show the privacy refusal. Ask `Do you offer gift wrapping?` to show the honest limitation and escalation.
6. **One thing that does not work yet:** requested exchange-size inventory is not in the supplied data, so the assistant creates an eligible size-exchange request but leaves availability “to be confirmed”; this is intentional rather than inventing stock.
