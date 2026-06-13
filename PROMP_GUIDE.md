A good approach is to have two reusable prompts:

1. **Before starting a feature** → planning and design prompt.
2. **Before finishing a feature** → review and quality assurance prompt.

These work across Codex, Claude Code, GitHub Copilot, Cursor, Windsurf, etc.

---

# 1. Before Building a Feature

Use this prompt before asking the AI to implement anything:

```text
You are a senior backend engineer.

Before writing code:

1. Analyze the existing codebase and architecture.
2. Identify all affected modules, services, APIs, database tables, and dependencies.
3. Explain the implementation plan step by step.
4. Ensure the design follows:
   - SOLID principles
   - Separation of concerns
   - DRY
   - KISS
   - Existing project conventions
5. Consider:
   - Security implications
   - Authentication and authorization
   - Validation
   - Error handling
   - Logging
   - Performance
   - Scalability
   - Backward compatibility
6. Identify edge cases and failure scenarios.
7. Highlight any ambiguity in requirements before implementation.
8. Suggest database migration changes if needed.
9. Explain the API contract changes if any.
10. Do not start coding until the implementation plan is presented and reviewed.

After presenting the plan, implement the feature using clean, maintainable, production-ready code.
```

---

# 2. After Building a Feature

Use this prompt after the implementation is complete:

```text
Act as a senior staff engineer performing a production readiness review.

Review the implemented feature and identify:

1. Logic bugs
2. Edge cases
3. Security vulnerabilities
4. Race conditions
5. Concurrency issues
6. Data consistency problems
7. Performance bottlenecks
8. Scalability concerns
9. Memory leaks
10. Error handling gaps
11. Missing validations
12. Authorization issues
13. API contract violations
14. Code duplication
15. Violations of SOLID, DRY, or Clean Architecture principles
16. Missing tests
17. Missing logging, monitoring, or observability
18. Missing documentation

Then:

- Suggest improvements.
- Refactor code where necessary.
- Generate or update tests.
- Verify backward compatibility.
- Verify migration safety.
- Verify production readiness.

Finally provide:

- Risk assessment
- Technical debt introduced
- Deployment considerations
- Rollback considerations
- Production readiness score out of 10
```

---

# 3. Final Verification Prompt (Very Useful)

Before committing:

```text
Perform a complete pull request review.

Review all modified files as if this were a real production PR.

Check for:

- Bugs
- Security issues
- Maintainability issues
- Readability issues
- Naming inconsistencies
- Architectural violations
- Missing tests
- Dead code
- Overengineering
- Underengineering

Provide:

1. Blocking issues
2. Recommended improvements
3. Nice-to-have improvements
4. Overall approval status

If issues are found, fix them and show the changes.
```

---

In practice, many senior engineers use a workflow like:

**Requirements → Planning Prompt → Implementation → Review Prompt → PR Review Prompt → Commit**


