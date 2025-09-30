#!/bin/bash
set -e

echo "🔧 Setting up nextflow-k8s-service workspace..."

# Check if uv is installed
if ! command -v uv &> /dev/null; then
    echo "❌ Error: uv package manager is not installed"
    echo "Please install uv first: https://github.com/astral-sh/uv"
    exit 1
fi

# Install dependencies
echo "📦 Installing dependencies..."
uv sync --group dev

# Copy .env file if it exists in the root repository
if [ -n "$CONDUCTOR_ROOT_PATH" ] && [ -f "$CONDUCTOR_ROOT_PATH/.env" ]; then
    echo "📄 Copying .env file from root repository..."
    cp "$CONDUCTOR_ROOT_PATH/.env" .env
    echo "✅ .env file copied"
else
    echo "⚠️  Warning: No .env file found in root repository"
    echo "   You may need to create a .env file with required configuration"
    echo "   See app/config.py for available settings"
fi

echo "✅ Workspace setup complete!"
echo ""
echo "Next steps:"
echo "  - Click the 'Run' button to start the development server"
echo "  - Access the API at http://localhost:8000/docs"
