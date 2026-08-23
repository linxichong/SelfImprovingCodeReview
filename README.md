# Self-Improving AI Code Reviewer

Feedback Loop を活用した自己改善型 AI Code Review Agent.

This project explores an AI code review agent that improves over time by turning developer feedback into reusable engineering memory.

The first goal is not to fine-tune a model. Instead, the system keeps the model stable and improves the review context through:

```text
AI Review
Human Feedback
Knowledge Extraction
Feedback Memory
Context Retrieval
Next Review
```

## Current Status

This repository is currently in the design phase.

The main design document is:

- `self-improving-ai-code-review-design.md`

## MVP Direction

The first implementation target is a minimal CLI reviewer:

```bash
python review.py --git-diff --output review.json
```

Initial scope:

- Read Git diff
- Build review context
- Call an LLM
- Return structured review JSON
- Prepare evaluation cases

Later phases will add feedback capture, memory candidates, human approval, retrieval, and GitHub/GitLab integration.

## Core Idea

モデルを学習させるのではなく、Feedback から Context を成長させる。