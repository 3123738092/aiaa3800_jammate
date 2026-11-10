# Reference creation implementation plan

**Goal:** Preserve the humming path and connect the team's original rhythm and text paths in the user's repository.
**Base:** 9da4027 on integration/style-llm-creation. Reference a63a0f5f579cbc36ed0dfa31874c8888e4873cbd.
**Architecture:** Extract the pinned Python source from this repository's Git objects into an ignored runtime directory. A local Python child imports the extracted code, while the web server calls it through a small JSON protocol. The adapters select original functions and package results, without editing their music algorithms.
**Approved boundary:** Settings may differ by entry. Genre experiments can be shown as separately labeled reference samples when the original input path cannot consume them.

- [x] Add and run tests for reference rhythm analysis/export and independent original text turns, including retry identity.
- [x] Connect original rhythm analysis, follow/vary/simplify, energy and bar count. Keep microphone and source playback behavior. Keep local humming path unchanged.
- [x] Use original text input, Agent, StateManager, plan validation and renderer. Keep state by work on the text page. Expose original starting BPM and supported instrument profiles; keep local rule mode explicit. Report missing model config honestly.
- [x] Collect reference style examples with the original style pattern generator. No replacement of recorded source anchors with the standalone style patterns.
- [ ] Verify actual browser playback/export, safe retries and input isolation. Verify the original LLM decision contract with a scripted provider if no authorized configuration exists. Provide handoff docs and push only to origin.
