# Developer Guide: Patterns, Rules & Skills

A pragmatic guide for developers and coding agents working in repositories gated by `scitools-hook`.

## The 60-second mental model

You write code, stage it, and commit. The pre-commit hook runs `scitools-hook check --staged`.

Here is what happens:

1. **Pre-existing debt never blocks.** If a routine had a cyclomatic complexity of 14 before you started, you are never forced to fix it. The gate marks it `pre-existing` and exits 0.
2. **Only regression blocks.** If your edit takes that routine from 14 to 15, or creates a brand new routine that exceeds the ceiling of 10, the commit is blocked (exit 1).
3. **Every finding tells you how to fix it.** A blocking report does not leave you guessing: each finding carries a `hint:` line suggesting the exact structural edit (`delete:`, `yagni:`, `shrink:`).
4. **Self-check while editing.** Run `scitools-hook check --worktree` while working so you never get surprised at commit time.

---

## Language support: what Understand analyzes

`scitools-hook` delegates static parsing to [SciTools Understand](https://scitools.com/). Understand analyzes **twelve languages across 58 file extensions**:

| Supported languages | File extensions |
| --- | --- |
| **Python** | `.py`, `.upy` |
| **C++ / C** | `.cpp`, `.cc`, `.cxx`, `.c`, `.h`, `.hpp`, `.hxx`, `.m`, `.mm`, `.cu` |
| **Web (JavaScript / TypeScript / PHP)** | `.js`, `.mjs`, `.cjs`, `.ts`, `.tsx`, `.php`, `.htm`, `.html`, `.css` |
| **Java** | `.java` |
| **C#** | `.cs` |
| **Ada, Fortran, Assembly, Basic, Jovial, Pascal, VHDL** | `.ada`, `.f90`, `.asm`, `.vb`, `.pas`, `.sql`, `.vhd` |

!!! note "Go, Rust, and unsupported languages"
    SciTools Understand does **not** support **Go** (`.go` files are skipped during analysis and will not produce findings). **Rust** (`.rs`) requires a Cargo project in Understand 8.0 and is not currently in the extension map. For polyglot teams, the clean code and lean architecture patterns below apply universally across Python, C++, JavaScript, and Go.

---

## Catchy Before & After examples

Every example below was measured and verified against `scitools-hook check`.

### 1. Cyclomatic complexity & deep nesting

- **Rules:** `routine.CyclomaticStrict` (max 10), `routine.MaxNesting` (max 3), `routine.CountParams` (max 5).
- **The symptom:** The "arrow anti-pattern" — deeply nested `if`/`else` ladders with high branch counts, difficult to test and expensive for AI context reasoning.
- **The remedy:** Invert conditions into guard clauses, extract focused sub-functions, or use lookup tables / strategy maps.

=== "Python"

    ```python
    # BEFORE (BLOCKED: CyclomaticStrict 11, MaxNesting 5, CountParams 7)
    def process_user_data(user_id, name, age, email, role, country, status):
        if user_id > 0:
            if age > 18:
                if role == "admin":
                    if status == "active":
                        if country == "US":
                            return "US Admin"
                        elif country == "UK":
                            return "UK Admin"
                        elif country == "DE":
                            return "DE Admin"
                        else:
                            return "Intl Admin"
        return "Guest"


    # AFTER (PASSES: CyclomaticStrict 2, MaxNesting 1, CountParams 1)
    ADMIN_REGIONS = {"US": "US Admin", "UK": "UK Admin", "DE": "DE Admin"}


    def is_eligible_admin(user) -> bool:
        return user.id > 0 and user.age > 18 and user.role == "admin" and user.status == "active"


    def process_user_data(user) -> str:
        if is_eligible_admin(user):
            return ADMIN_REGIONS.get(user.country, "Intl Admin")
        return "Guest"
    ```

=== "C++"

    ```cpp
    // BEFORE (BLOCKED: CyclomaticStrict 11, MaxNesting 4, CountParams 6)
    std::string handle_request(int id, int level, int opt, bool flagA, bool flagB, bool flagC) {
        if (id > 100) {
            if (level == 1) {
                if (flagA && flagB) {
                    return "A+B";
                } else if (flagA || flagC) {
                    return "A|C";
                }
            } else if (level == 2) {
                if (opt == 10) return "L2-10";
                else if (opt == 20) return "L2-20";
                else if (opt == 30) return "L2-30";
                else return "L2-default";
            }
        }
        return "default";
    }

    // AFTER (PASSES: CyclomaticStrict 3, MaxNesting 1, CountParams 1)
    struct RequestConfig {
        int id;
        int level;
        int opt;
        bool flagA;
        bool flagB;
        bool flagC;
    };

    std::string level_two_label(int opt) {
        switch (opt) {
            case 10: return "L2-10";
            case 20: return "L2-20";
            case 30: return "L2-30";
            default: return "L2-default";
        }
    }

    std::string handle_request(const RequestConfig& req) {
        if (req.id <= 100) return "default";
        if (req.level == 1) {
            if (req.flagA && req.flagB) return "A+B";
            if (req.flagA || req.flagC) return "A|C";
        } else if (req.level == 2) {
            return level_two_label(req.opt);
        }
        return "default";
    }
    ```

=== "JavaScript"

    ```javascript
    // BEFORE (BLOCKED: CyclomaticStrict 11, MaxNesting 5, CountParams 6)
    function calculateDiscount(price, customerType, isHoliday, isMember, couponCode, region) {
        let discount = 0;
        if (price > 50) {
            if (customerType === 'vip') {
                if (isHoliday) {
                    if (isMember) {
                        discount = 0.30;
                    } else {
                        discount = 0.25;
                    }
                } else if (couponCode === 'SAVE10') {
                    discount = 0.20;
                } else {
                    discount = 0.10;
                }
            } else if (customerType === 'regular' && isMember) {
                discount = 0.05;
            }
        }
        return price * (1 - discount);
    }

    // AFTER (PASSES: CyclomaticStrict 2, MaxNesting 1, CountParams 1)
    function getVipDiscount({ isHoliday, isMember, couponCode }) {
        if (isHoliday) return isMember ? 0.30 : 0.25;
        if (couponCode === 'SAVE10') return 0.20;
        return 0.10;
    }

    function calculateDiscount(opts) {
        if (opts.price <= 50) return opts.price;
        let rate = 0;
        if (opts.customerType === 'vip') {
            rate = getVipDiscount(opts);
        } else if (opts.customerType === 'regular' && opts.isMember) {
            rate = 0.05;
        }
        return opts.price * (1 - rate);
    }
    ```

=== "Go"

    ```go
    // IDIOMATIC PATTERN (Clean Go guard clauses & option structs)
    type OrderOptions struct {
        ID       int
        Level    int
        Opt      int
        FlagA    bool
        FlagB    bool
        FlagC    bool
    }

    // Flattened control flow using guard clauses instead of nested conditionals
    func HandleRequest(opts OrderOptions) string {
        if opts.ID <= 100 {
            return "default"
        }
        if opts.Level == 1 {
            if opts.FlagA && opts.FlagB {
                return "A+B"
            }
            if opts.FlagA || opts.FlagC {
                return "A|C"
            }
        }
        if opts.Level == 2 {
            return handleLevelTwo(opts.Opt)
        }
        return "default"
    }
    ```

---

### 2. Scattered state: one decision copied in many files

- **Rule:** `structure.duplicate_definition` (configurable threshold via `duplicate_definitions`).
- **The symptom:** Constants, magic values, or path definitions repeated across multiple modules (e.g. `MAX_RETRIES = 5` defined in 4 different files).
- **Why it matters:** When `MAX_RETRIES` needs to change, reading any one file does not reveal that 3 other files hardcode the same number. If one file is updated and the others are missed, silent desynchronization occurs.
- **The remedy:** Declare the shared value in a central constants or config module and import it.

=== "Python"

    ```python
    # BEFORE (REPORTED: duplicate_definition across service_a, service_b, service_c, service_d)
    # service_a.py
    MAX_RETRIES = 5

    # service_b.py
    MAX_RETRIES = 5

    # AFTER (PASSES: single source of truth)
    # constants.py
    MAX_RETRIES = 5

    # service_a.py & service_b.py
    from constants import MAX_RETRIES
    ```

=== "C++"

    ```cpp
    // BEFORE (Duplicated constant declarations across headers)
    // client_a.hpp
    constexpr int kMaxRetries = 5;

    // client_b.hpp
    constexpr int kMaxRetries = 5;

    // AFTER (Centralized in config header)
    // config.hpp
    namespace config {
        inline constexpr int kMaxRetries = 5;
    }
    ```

=== "JavaScript"

    ```javascript
    // BEFORE (Hardcoded literals across multiple modules)
    // apiService.js
    const DEFAULT_TIMEOUT_MS = 5000;

    // authService.js
    const DEFAULT_TIMEOUT_MS = 5000;

    // AFTER (Centralized export)
    // config.js
    export const DEFAULT_TIMEOUT_MS = 5000;
    ```

=== "Go"

    ```go
    // BEFORE (Separate packages declaring duplicate limits)
    // package worker: const MaxQueueRetries = 5
    // package api:    const MaxQueueRetries = 5

    // AFTER (Shared package config)
    package config

    const MaxQueueRetries = 5
    ```

---

### 3. Lean code: duplicate blocks & similar twins

- **Rules:** `structure.duplicate_block`, `structure.similar_routine`.
- **The symptom:**
    - Identical copy-pasted blocks of 8–12+ lines across files.
    - Twin functions (e.g., `calculate_us_tax` and `calculate_eu_tax`) that are 90%+ identical token-for-token.
- **The remedy (`delete:` tag):** Extract the shared block into a single helper; parameterize twin routines with arguments rather than maintaining parallel copies.

=== "Python"

    ```python
    # BEFORE (BLOCKED: structure.similar_routine - twin routines)
    def fetch_user_metrics(user_id: int, api_client):
        token = api_client.get_token()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        response = api_client.get(f"/api/v1/users/{user_id}/metrics", headers=headers)
        return response.json().get("data", [])


    def fetch_team_metrics(team_id: int, api_client):
        token = api_client.get_token()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        response = api_client.get(f"/api/v1/teams/{team_id}/metrics", headers=headers)
        return response.json().get("data", [])


    # AFTER (PASSES: delete redundant twin, parameterize difference)
    def fetch_entity_metrics(entity_type: str, entity_id: int, api_client):
        token = api_client.get_token()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        response = api_client.get(f"/api/v1/{entity_type}/{entity_id}/metrics", headers=headers)
        return response.json().get("data", [])
    ```

=== "C++"

    ```cpp
    // BEFORE (BLOCKED: duplicate block of transformation logic in two methods)
    void ParserA::parse(const std::vector<std::string>& lines) {
        // ... 12 lines of token sanitization and buffer normalization ...
    }
    void ParserB::parse(const std::vector<std::string>& lines) {
        // ... identical 12 lines of token sanitization and buffer normalization ...
    }

    // AFTER (Extracted into shared utility)
    namespace utils {
        std::vector<std::string> sanitize_tokens(const std::vector<std::string>& lines);
    }
    ```

=== "JavaScript"

    ```javascript
    // BEFORE (BLOCKED: structure.similar_routine)
    function exportCustomersToCsv(customers) {
        const header = "id,name,email\n";
        const rows = customers.map(c => `${c.id},"${c.name}","${c.email}"`).join("\n");
        return header + rows;
    }
    function exportVendorsToCsv(vendors) {
        const header = "id,name,email\n";
        const rows = vendors.map(v => `${v.id},"${v.name}","${v.email}"`).join("\n");
        return header + rows;
    }

    // AFTER (Unified generic exporter)
    function exportRecordsToCsv(records) {
        const header = "id,name,email\n";
        const rows = records.map(r => `${r.id},"${r.name}","${r.email}"`).join("\n");
        return header + rows;
    }
    ```

=== "Go"

    ```go
    // IDIOMATIC PATTERN: Table-driven or higher-order function rather than twin routines
    func FetchEntityMetrics[T any](ctx context.Context, endpoint string, client *http.Client) ([]T, error) {
        req, err := http.NewRequestWithContext(ctx, "GET", endpoint, nil)
        if err != nil {
            return nil, err
        }
        req.Header.Set("Accept", "application/json")
        // single unified request handler
        return executeAndDecode[T](client, req)
    }
    ```

---

### 4. Lean code: pass-through wrappers & dead code

- **Rules:** `structure.pass_through`, `structure.unused_parameter`.
- **The symptom:**
    - Functions whose entire body consists of forwarding all arguments to another single function without transformation (`pass_through`).
    - Declared parameters that are never referenced inside the routine body (`unused_parameter`).
- **The remedy (`delete:` or `yagni:` tag):** Delete the redundant wrapper layer; call the target directly. Remove unused parameters or prefix with `_` if part of a required external signature.

=== "Python"

    ```python
    # BEFORE (BLOCKED: structure.pass_through and structure.unused_parameter)
    def calculate_tax(amount: float, tax_table, unused_meta) -> float:
        return tax_table.compute(amount)


    # AFTER (PASSES: call tax_table.compute directly or drop dead wrapper & param)
    def calculate_tax(amount: float, tax_table) -> float:
        return tax_table.compute(amount)
    ```

=== "C++"

    ```cpp
    // BEFORE (Pass-through forwarding function adding call overhead and indirection)
    int Account::getBalance(int accountId) {
        return m_ledger.getBalance(accountId);
    }

    // AFTER (Expose ledger interface directly or call m_ledger.getBalance(accountId))
    ```

=== "JavaScript"

    ```javascript
    // BEFORE (Redundant wrapper function)
    const getUserName = (user) => user.getName();

    // AFTER (Delete wrapper, use user.getName() directly at call sites)
    ```

=== "Go"

    ```go
    // IDIOMATIC PATTERN: Avoid empty forwarding wrappers; use composition or interface embedding
    type Service struct {
        *Repository // embedded directly rather than writing 10 pass-through getter methods
    }
    ```

---

## How the 5 packaged skills get used

When you run `scitools-hook install-skills`, five vendor-neutral `SKILL.md` documents are installed to `.agents/skills` (or `--dir .claude/skills`).

Autonomous coding assistants (Antigravity, Claude Code, Cursor) and human developers use them across five distinct workflows:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        THE 5 PACKAGED SKILLS                           │
├────────────────────────────────────────────────────────────────────────┤
│                                                                        │
│   1. scitools-onboard   ──► First-time repository enablement:          │
│                             derives limits from measurements, not      │
│                             assumptions, and captures initial baseline │
│                                                                        │
│   2. scitools-gate      ──► Commit verification protocol:              │
│                             runs pre-commit self-checks, reads JSON    │
│                             hints, refactors code. REFUSES to touch    │
│                             or relax configuration                     │
│                                                                        │
│   3. scitools-improve   ──► Systematic debt reduction:                 │
│                             iterative commit-by-commit refactoring loop│
│                             tightening the adaptive baseline           │
│                                                                        │
│   4. scitools-adapt     ──► Policy adjustment with evidence:           │
│                             walks the 6-rung ladder when a limit is    │
│                             genuinely wrong for the repository         │
│                                                                        │
│   5. scitools-tune      ──► Lean code & duplication calibration:       │
│                             empirically tunes duplicate line minimums, │
│                             similarity thresholds, and trust floors    │
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘
```

### Daily workflow with skills

1. **Before writing code**: The assistant reads `AGENTS.md` (generated by `scitools-hook agent-rules --write AGENTS.md`) so it knows all thresholds and the lean ladder.
2. **While writing code**: The assistant invokes `scitools-gate` to check edits via `scitools-hook check --worktree --format json`.
3. **If blocked**: The assistant reads the `hint:`, refactors the code to satisfy the metric, and re-checks until `blocking_count == 0`.
4. **When lowering debt**: You run `scitools-improve` to identify high-churn complex routines and refactor them down one commit at a time.
5. **When tuning lean detection**: You run `scitools-tune` to measure repository duplication and adjust `duplicates_min_lines` or `similar_threshold` based on empirical evidence.

---

## Developer cheat sheet

```bash
# Check your working tree while editing (uncommitted edits)
scitools-hook check --worktree

# Check what is staged for commit (the pre-commit hook runs this)
scitools-hook check --staged

# Check machine-readable JSON output with remediation hints
scitools-hook check --worktree --format json

# Verify Understand installation, license, and recorded capabilities
scitools-hook doctor

# Generate agent rules into your instructions file
scitools-hook agent-rules --write AGENTS.md

# Install all 5 agent skills
scitools-hook install-skills

# Emergency bypass for one commit (prints notice)
SCITOOLS_HOOK_SKIP=1 git commit -m "hotfix"

# Treat infrastructure/missing license as warning (findings still block)
export SCITOOLS_HOOK_SOFT_FAIL=1
```
