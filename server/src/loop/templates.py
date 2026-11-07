"""Message templates Smara creates in each WhatsApp account (Meta reviews them per account)."""

# v3: names the business (one shared number can serve many shops), English + Hindi, plain account-update wording.
# Server .env once approved: WA_REMINDER_TEMPLATE=smara_account_update  WA_REMINDER_PARAMS=customer,shop,invoice,amount,due
#                            WA_OWNER_TEMPLATE=smara_owner_update
_UPDATE_EXAMPLE = {"body_text": [["Ravi Kirana Store", "Sharma Traders", "INV-101", "Rs 12,500", "22 Oct"]]}
TEMPLATES_V3 = [
    {"name": "smara_account_update", "language": "en", "category": "UTILITY", "components": [{
        "type": "BODY", "example": _UPDATE_EXAMPLE,
        "text": "Hello {{1}}, this is an account update from {{2}}: invoice {{3}} for {{4}} was due on {{5}} and is shown as "
                "open in our records. If it is already settled, or you would like to discuss it, please reply to this message."}]},
    {"name": "smara_account_update", "language": "hi", "category": "UTILITY", "components": [{
        "type": "BODY", "example": _UPDATE_EXAMPLE,
        "text": "नमस्ते {{1}}, यह {{2}} की ओर से खाते की जानकारी है: बिल {{3}}, राशि {{4}}, जिसकी तारीख {{5}} थी, हमारे रिकॉर्ड में "
                "अभी बाकी दिख रहा है। अगर भुगतान हो चुका है या आप बात करना चाहते हैं, तो कृपया इसी मैसेज का जवाब दें।"}]},
    {"name": "smara_owner_update", "language": "en", "category": "UTILITY", "components": [{
        "type": "BODY", "example": {"body_text": [["3"]]},
        "text": "You have {{1}} update(s) from your Smara assistant waiting for your review. Reply to this message to see them."}]},
]
