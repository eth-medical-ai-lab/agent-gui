# Contributing to AgentGUI

Thanks for your interest in improving AgentGUI! Any ideas, feedbacks, reports, or changes are welcome.

## Ways to contribute

For feature requests and issues, please open a [GitHub issue](../../issues).

## Reporting bugs

Please include

- Your OS, Python version (`python --version`), and Hermes version (`hermes --version`).
- The inference backend you're using.
- Steps to reproduce, plus any relevant logs from terminal.
- What you expected to happen and what actually happened.

## Development setup

1. Fork and clone the repository, then create a branch for your change:

   ```bash
   git checkout -b my-feature
   ```

2. Install the project with the development dependencies and build the frontend.
   See [Step 3: Install AgentGUI & run](README.md#step-3-install-agentgui--run) for
   the full instructions. In short:

   ```bash
   pip install -e ".[dev]"          # backend + test dependencies
   cd frontend && npm install && npm run build && cd ..
   ```

3. Run the app in [dev mode (hot-reload)](README.md#dev-mode-hot-reload) while you work:

   ```bash
   bash dev.sh
   ```

## Running the tests

Please run the test suite before opening a pull request:

```bash
pip install -e ".[dev]"
python -m pytest tests/
```

A few tests import the locally installed Hermes agent (`~/.hermes/hermes-agent`), so
they need the [Hermes prerequisite](README.md#prerequisites) in place.

If any regression happenens, please note it in your PR.

## Pull request workflow

1. Please keep each pull request small and focused if possible.
2. Make sure the tests pass and the frontend builds cleanly.
3. Write a succint PR description: what changed, why, and any
   related issue (e.g. `Closes #123`).

## Coding conventions

- **Python** targets 3.12+. Match the style of the surrounding code and keep functions
  small and readable.
- **Frontend** lives under [`frontend/`](frontend/); follow the existing component and
  formatting conventions there.

## License

By contributing, you agree that your contributions will be licensed under the
[MIT License](LICENSE) that covers this project.
