---
applyTo: "**/*.ps1,**/*.psm"
---

# GitHub Copilot Instructions for AzureVMSkuAlternatives


## Repository Overview
This repository contains tools and scripts for finding alternative Azure VM SKUs based on various criteria such as pricing, performance, and availability.

## Code Standards and Practices

### Language and Framework
- Primary language: PowerShell
- Target Azure PowerShell modules and Azure CLI compatibility
- Follow PowerShell best practices and naming conventions

### Coding Style
- Use approved PowerShell verbs (Get, Set, New, Remove, etc.)
- Include comment-based help for all functions
- Use meaningful variable names with PascalCase for parameters
- Include error handling with try-catch blocks
- Add verbose and debug output for troubleshooting

### Azure-Specific Guidelines
- Always use the latest Azure PowerShell module syntax
- Include region and subscription context in examples
- Handle Azure authentication appropriately
- Cache API responses when appropriate to minimize costs
- Include error handling for common Azure errors (quota limits, regio`Pnal availability)

### Documentation
- Update README.md when adding new scripts or features
- Include usage examples in comment-based help
- Document any prerequisites (modules, permissions, API versions)
- Provide sample output for complex operations

### Testing
- Include example commands that can be tested
- Validate input parameters
- Test with multiple Azure regions
- Consider different subscription types and quotas

### Security
- Never hardcode credentials or subscription IDs
- Use Azure managed identities when possible
- Implement proper RBAC checks
- Sanitize output to remove sensitive information

## Common Tasks
- When generating VM SKU comparison scripts, include pricing tier information
- When querying Azure resources, handle pagination for large result sets
- When suggesting alternatives, consider both cost and performance metrics
- Include filters for VM family, vCPU count, memory, and region availability// filepath: .github/copilot-instructions.md
# GitHub Copilot Instructions for AzureVMSkuAlternatives

## Repository Overview
This repository contains tools and scripts for finding alternative Azure VM SKUs based on various criteria such as pricing, performance, and availability.

## Code Standards and Practices

### Language and Framework
- Primary language: PowerShell
- Target Azure PowerShell modules and Azure CLI compatibility
- Follow PowerShell best practices and naming conventions

### Coding Style
- Use approved PowerShell verbs (Get, Set, New, Remove, etc.)
- Include comment-based help for all functions
- Use meaningful variable names with PascalCase for parameters
- Include error handling with try-catch blocks
- Add verbose and debug output for troubleshooting

### Azure-Specific Guidelines
- Always use the latest Azure PowerShell module syntax
- Include region and subscription context in examples
- Handle Azure authentication appropriately
- Cache API responses when appropriate to minimize costs
- Include error handling for common Azure errors (quota limits, regional availability)

### Documentation
- Update README.md when adding new scripts or features
- Include usage examples in comment-based help
- Document any prerequisites (modules, permissions, API versions)
- Provide sample output for complex operations

### Testing
- Include example commands that can be tested
- Validate input parameters
- Test with multiple Azure regions
- Consider different subscription types and quotas

### Security
- Never hardcode credentials or subscription IDs
- Use Azure managed identities when possible
- Implement proper RBAC checks
- Sanitize output to remove sensitive information

## Common Tasks
- When generating VM SKU comparison scripts, include pricing tier information
- When querying Azure resources, handle pagination for large result sets
- When suggesting alternatives, consider both cost and performance metrics
- Include filters for VM family, vCPU count, memory, and region availability