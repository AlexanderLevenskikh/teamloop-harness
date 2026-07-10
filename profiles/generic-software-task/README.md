# Generic Software Task Profile

This profile supports general software engineering tasks:

- Small bug fixes
- Small API replacements
- Test fixes
- Typecheck error fixes
- Small refactors

## Default Allowed Writes

```
src/**
tests/**
.teamloop/**
```

## Default Forbidden Writes

```
.git/**
node_modules/**
dist/**
build/**
```

## Gate Commands

| Name | Type | Required |
|------|------|----------|
| scope | built-in | yes |

## Task Slicing

- Max files per task: 5
- Max risk: medium

## Discovery Questions

1. **What should be changed?** (required)
2. **What command verifies the change?** (optional)
3. **Which files or directories are affected?** (optional)
