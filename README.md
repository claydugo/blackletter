# Blackletter

Remove copyrighted material from legal case law PDFs.

A reference to blackletter law, this tool removes proprietary annotations from judicial opinions—specifically Westlaw key citations, headnotes, and other copyrighted materials—while preserving the authentic opinion text.

## Installation

```bash
git clone https://github.com/yourusername/blackletter
cd blackletter
pip install -e .
```

## Quick Start

**Command line:**
```bash
blackletter path/to/opinion.pdf -o output/folder -p 737
```

**Python:**
```python
from blackletter import BlackletterPipeline

pipeline = BlackletterPipeline()
redacted_pdf, opinions_dir = pipeline.process("opinion.pdf")
```

## How It Works

The pipeline operates in four phases:

1. **Scanning (Phase 1)**: Uses YOLO to detect copyrighted elements (captions, key cites, headnotes, etc.)
2. **Planning (Phase 2)**: State machine determines which text spans to redact
3. **Execution (Phase 3)**: Applies redactions and masks to the PDF
4. **Extraction (Phase 4)**: Splits opinions into individual files

## Configuration

```python
from blackletter import BlackletterPipeline
from blackletter.config import RedactionConfig

config = RedactionConfig(
    confidence_threshold=0.25,
    dpi=200,
    MODEL_PATH="best.pt"
)

pipeline = BlackletterPipeline(config)
redacted_pdf, opinions_dir = pipeline.process("opinion.pdf")
```

## Requirements

- Python 3.8+
- YOLO model (`best.pt`)
- PDFs must be text-based (not scanned images)

## License

MIT

## Contributing

Contributions welcome! Please submit issues and PRs.

[//]: # (blackletter /Users/Palin/Code/gemini/src/output/processed/p3d/536/737/opinions.pdf -o ./output)
