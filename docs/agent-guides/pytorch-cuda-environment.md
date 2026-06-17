# PyTorch / CUDA Environment Planning Guide

This guide is for agents producing an `EnvironmentPlan` for PyTorch projects
that may use CUDA. It is guidance only; Core policy, Builder, and Verifier are
the enforcement layer.

## Boundaries

- Do not choose versions from memory when repository files or official project
  docs provide exact requirements.
- Do not encode a long-term PyTorch/CUDA compatibility matrix in AutoAD Core.
- Do not install GPU drivers, kernel modules, or system CUDA toolkit without
  explicit human approval.
- Do not download model weights during validation. Asset downloads belong to
  `AssetPlan`.

## Required Distinctions

- GPU driver: host capability. Record it as evidence; do not mutate it.
- Wheel CUDA runtime: package selection, often encoded in package index or wheel
  tag.
- System CUDA toolkit: only needed for custom CUDA extension builds; approval is
  required.
- Compiler toolchain: system mutation risk; approval is required.

## Plan Evidence

A CUDA `EnvironmentPlan` should cite evidence for:

- dependency files such as `pyproject.toml`, `requirements.txt`, lockfiles, or
  environment YAML files;
- repository README install instructions;
- source files that import `torch`, `torchvision`, custom CUDA extensions, or
  compiled ops;
- host capability evidence, if GPU validation is required;
- previous error evidence when producing a revision.

## Build Planning

- Use explicit package indexes when CUDA wheels require them.
- Keep version choices in the plan, not in Builder fallback logic.
- Do not let Builder silently upgrade, downgrade, or switch indexes.
- Keep validation network disabled.
- If custom CUDA extension compilation is required, mark the relevant step as
  requiring approval.

## Validation Planning

Include deterministic validation steps for:

- Python runtime version;
- package inventory for required packages;
- imports such as `torch`, `torchvision`, and project modules;
- GPU availability when the experiment requires GPU;
- a minimal tensor compute probe when CUDA execution is required;
- project smoke command without implicit weight download;
- repository clean check before and after execution.

## Asset Boundary

Backbone weights, checkpoints, tokenizers, indexes, and pretrained parameters
must be represented by `AssetPlan`, not hidden inside environment validation.
Validation may only prove that prepared assets can be read or loaded offline.
