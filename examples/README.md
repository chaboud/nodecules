# Nodecules Example Graphs

This directory contains example graphs to help you get started with Nodecules. Each graph demonstrates different features and patterns.

## 🚀 Quick Start

1. **Start Nodecules**: `docker-compose up -d`
2. **Go to web interface**: http://localhost:3000
3. **Import an example**:
   - Click "Import Graph" (green button)
   - Select one of the `.nodecules.json` files from this folder
   - Click "Execute" to test it

## 📁 Available Examples

### 1. **Simple Text Processing** (`simple_text_processing.nodecules.json`)
- **Difficulty**: Beginner
- **Features**: Basic input → transform → output pipeline
- **Demonstrates**: Text transformation, node connections
- **Try it**: Import and run with any text input

### 2. **Text Analysis Pipeline** (`text_analysis_pipeline.nodecules.json`)
- **Difficulty**: Intermediate  
- **Features**: Multi-step text processing with filtering and analysis
- **Demonstrates**: Text filtering, word counting, result concatenation
- **Try it**: Run with mixed-case text containing numbers

### 3. **Context Management Demo** (`context_management_demo.nodecules.json`)
- **Difficulty**: Beginner
- **Features**: Context storage, retrieval, and key generation
- **Demonstrates**: Stateless workflow patterns, data persistence
- **Try it**: Shows how to store/retrieve conversation data

### 4. **Claude Chat with Context** (`claude_chat_with_context.nodecules.json`)
- **Difficulty**: Advanced
- **Features**: Full conversation system with Claude AI
- **Requirements**: Set `ANTHROPIC_API_KEY` environment variable
- **Demonstrates**: AI chat, context continuity, real conversation flow
- **Try it**: Have a multi-turn conversation with Claude

## 🔧 Configuration

### For Claude Examples:
```bash
# Set your API key
export ANTHROPIC_API_KEY="your-anthropic-key-here"

# Or add to docker-compose.yml:
environment:
  - ANTHROPIC_API_KEY=your-anthropic-key-here
```

## 📖 Learning Path

1. **Start here**: `simple_text_processing.nodecules.json`
   - Learn basic node connections
   - Understand input/output flow

2. **Next try**: `text_analysis_pipeline.nodecules.json`  
   - See multi-step processing
   - Explore different node types

3. **Then explore**: `context_management_demo.nodecules.json`
   - Learn context storage patterns
   - Understand stateless workflows

4. **Advanced**: `claude_chat_with_context.nodecules.json`
   - Full AI integration
   - Production-ready patterns

## 💡 Tips

- **Modify examples**: Change parameters, add nodes, experiment!
- **Export your graphs**: Save your modifications as new examples
- **Mix and match**: Copy nodes between different example graphs
- **Debug mode**: Use the "Debug JSON" format to inspect data flow

## 🎯 Next Steps

After trying these examples:

1. **Build your own graphs** using the visual editor
2. **Create custom workflows** for your specific needs  
3. **Combine multiple examples** into larger pipelines
4. **Share your graphs** by exporting them as JSON

## 📚 More Resources

- **API Documentation**: http://localhost:8000/docs
- **Node Reference**: Check available nodes in the palette
- **Architecture Guide**: See `/backend/architecture.md` for technical details