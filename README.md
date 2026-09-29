# SONiC ChaosLab

> Learn networking by breaking it.

An interactive, AI-assisted CLI that teaches how SONiC behaves under failure. It
explains a concept on a live virtual SONiC lab, injects one controlled failure,
shows the real before/after state, and has an LLM explain the observed impact in
plain language. One-click reset restores the lab.

Full documentation is generated in the final build stage. For the product spec see
[PRODUCT.md](PRODUCT.md).

## Quickstart (no lab, no keys)

```bash
make setup && make run
```

This runs the full lesson loop against a mock lab and a deterministic fake model —
no containerlab, no Docker, no API keys required.

## License

Apache-2.0. See [LICENSE](LICENSE).
