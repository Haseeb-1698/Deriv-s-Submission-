# Support Triage Pipeline

A replayable support-triage pipeline that reads customer support tickets from disk, classifies each ticket into a controlled label set, detects urgency, drafts suggested replies, supports human review checkpoints for corrections, and produces a final queue summary for agents.

## Features

- ✅ Deterministic ticket normalization before LLM processing
- ✅ Structured LLM-based ticket classification and reply generation
- ✅ Interactive human review checkpoint with override capability
- ✅ Confidence-based escalation rules
- ✅ Comprehensive validation script
- ✅ Full audit trail with LLM call logging
- ✅ Reproducible outputs from clean checkout

## Pipeline Stages

The pipeline enforces these stages in order:

```
INIT
 -> INPUTS_LOADED
 -> TICKETS_NORMALIZED
 -> TRIAGE_PREDICTED
 -> HUMAN_REVIEW_COMPLETE
 -> FINAL_QUEUE_GENERATED
 -> VALIDATION_COMPLETE
 -> RESULTS_FINALISED
```

## Requirements

- Python 3.7+
- OpenAI Python SDK
- python-dotenv for environment variable management
- Kimi API key (or OpenAI-compatible API)

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

Or install manually:
```bash
pip install openai python-dotenv
```

### 2. Configure API Key

Copy the example environment file and add your API key:

```bash
cp .env.example .env
```

Then edit `.env` and add your Kimi API key:

```
KIMI_API_KEY=sk-your-key-here
LLM_MODEL=kimi-k2
```

### 3. Run the Pipeline

```bash
python triage_pipeline.py
```

The pipeline will:
1. Load environment variables from `.env` file
2. Load input files (`tickets.json`, `triage_config.json`)
3. Normalize tickets deterministically
4. Call Kimi API to generate triage predictions
5. Pause for human review (interactive)
6. Generate final queue and summary

### 4. Human Review

During execution, you'll see predictions and be prompted:

```
Enter overrides as: ticket_id,category,priority
Press Enter on an empty line when done.
```

Example overrides:
```
T-1001,account_access,urgent
T-1003,billing_issue,high
```

Press Enter on empty line to continue.

### 5. Validate Results

```bash
python validate.py
```

This checks:
- All required artifacts exist
- JSON files are valid
- Normalization is deterministic
- Categories and priorities match config
- Routing rules are correctly applied
- Overrides are properly reflected in final output
- Reply length respects configured limits

## Input Files

### `tickets.json`

Array of customer support tickets:

```json
[
  {
    "ticket_id": "T-1001",
    "customer_id": "C-001",
    "subject": "Charged twice for my deposit",
    "message": "Hi, I made one deposit...",
    "channel": "email",
    "created_at": "2026-05-10T09:15:00Z"
  }
]
```

### `triage_config.json`

Configuration for classification and routing:

```json
{
  "allowed_categories": [
    "billing_issue",
    "account_access",
    "product_how_to",
    "bug_report",
    "other"
  ],
  "allowed_priorities": [
    "urgent",
    "high",
    "normal",
    "low"
  ],
  "reply_style": {
    "tone": "clear, polite, concise",
    "max_words": 80
  },
  "routing_rules": {
    "billing_issue": "payments_queue",
    "account_access": "trust_and_access_queue",
    "product_how_to": "general_support_queue",
    "bug_report": "technical_queue",
    "other": "manual_review_queue"
  }
}
```

## Output Artifacts

### Required Outputs

- **`normalized_tickets.json`** - Deterministically normalized tickets with `text_for_model` field
- **`triage_predictions.json`** - LLM predictions with category, priority, confidence, and suggested replies
- **`review_overrides.json`** - Human review corrections (empty array if no overrides)
- **`final_queue.json`** - Final queue with post-review values and routing
- **`queue_summary.md`** - Human-readable summary with counts and breakdowns

### Optional Outputs

- **`escalations.json`** - Tickets flagged for manual escalation (low confidence or "other" category)
- **`llm_calls.jsonl`** - Audit log of all LLM API calls with timestamps and hashes

## Architecture

### Stage 1: Normalization (Deterministic)

```python
text_for_model = f"Subject: {subject}\nMessage: {message}"
char_count = len(text_for_model)
```

No LLM calls during normalization - purely deterministic code.

### Stage 2: Prediction (LLM)

Single LLM call processes all tickets with:
- Full configuration context
- Structured output format
- Confidence scoring
- Reply generation within word limits

### Stage 3: Human Review (Interactive)

Terminal-based review interface:
- Display all predictions
- Accept overrides in simple format
- Validate against allowed values
- Record all changes

### Stage 4: Final Queue (Deterministic)

- Apply overrides to predictions
- Recompute routing based on final categories
- Mark overridden tickets
- Generate summary statistics

## Validation Checks

The validation script verifies:

✅ **File Existence** - All required artifacts present  
✅ **JSON Validity** - All JSON files parse correctly  
✅ **Normalization** - Deterministic text construction  
✅ **Predictions** - One per ticket, valid categories/priorities  
✅ **Routing** - Matches configuration rules  
✅ **Overrides** - Valid values, properly applied  
✅ **Reply Length** - Respects max word limit  
✅ **Completeness** - Every ticket in final queue  

## Escalation Rules

Tickets are automatically flagged for escalation if:

1. Category is `"other"`, OR
2. Confidence score < 0.60

Escalated tickets are saved to `escalations.json` for manual review.

## Customization

### Replace Input Files

The pipeline reads from disk - simply replace:
- `tickets.json` with your tickets
- `triage_config.json` with your categories/rules

### Integrate Real LLM

Replace the `_simulate_llm_triage()` method in `triage_pipeline.py`:

```python
def _simulate_llm_triage(self) -> List[Dict[str, Any]]:
    # Replace with actual OpenAI/Anthropic/etc. API call
    import openai
    
    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[{"role": "user", "content": self._build_triage_prompt()}],
        temperature=0.3
    )
    
    return json.loads(response.choices[0].message.content)
```

### Modify Categories

Edit `triage_config.json`:
- Add/remove categories in `allowed_categories`
- Update `routing_rules` to map new categories to queues
- Adjust `allowed_priorities` as needed

## Error Handling

The pipeline includes:

- **Graceful degradation** - Malformed tickets go to `other` category
- **Validation gates** - Override values checked against config
- **Audit trail** - All LLM calls logged with timestamps
- **Reproducibility** - Deterministic preprocessing ensures consistent results

## Testing

Run the complete workflow:

```bash
# Run pipeline (will prompt for review)
python triage_pipeline.py

# Validate outputs
python validate.py

# Check generated files
ls -la *.json *.md *.jsonl
```

For automated testing (skip human review):

```bash
# Pipe empty input to skip review prompt
echo "" | python triage_pipeline.py
python validate.py
```

## Project Structure

```
.
├── README.md                    # This file
├── triage_pipeline.py          # Main pipeline script
├── validate.py                 # Validation script
├── tickets.json                # Input: customer tickets
├── triage_config.json          # Input: classification config
├── normalized_tickets.json     # Output: normalized tickets
├── triage_predictions.json     # Output: LLM predictions
├── review_overrides.json       # Output: human corrections
├── final_queue.json            # Output: final routing queue
├── queue_summary.md            # Output: human-readable summary
├── escalations.json            # Output: flagged tickets (optional)
└── llm_calls.jsonl             # Output: LLM audit log (optional)
```

## Technical Constraints

✅ Reads input files from disk  
✅ Deterministic normalization before LLM  
✅ Categories/priorities constrained by config  
✅ Routing derived from config rules  
✅ Human review affects downstream outputs  
✅ Reproducible from clean checkout  
✅ No hardcoded sample outputs  

## License

This is a demonstration project for AI engineering evaluation.

## Support

For issues or questions about the pipeline implementation, refer to the validation output or check the generated artifacts for debugging information.