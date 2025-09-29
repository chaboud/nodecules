#!/usr/bin/env python3
"""
Simple test script to verify Bedrock bearer token authentication.
Run this to test your AWS_BEARER_TOKEN_BEDROCK configuration.

Usage:
    export AWS_BEARER_TOKEN_BEDROCK=your-api-key
    python test_bedrock.py
"""

import os
import sys
import asyncio

async def test_bedrock():
    """Test Bedrock integration with bearer token."""
    
    # Check if bearer token is set
    bearer_token = os.getenv('AWS_BEARER_TOKEN_BEDROCK')
    if not bearer_token:
        print("❌ AWS_BEARER_TOKEN_BEDROCK not set")
        print("Set it with: export AWS_BEARER_TOKEN_BEDROCK=your-api-key")
        return False
    
    print(f"✅ Bearer token found: {bearer_token[:20]}...")
    
    try:
        # Import our Bedrock adapter
        from nodecules.core.smart_context import BedrockAdapter
        
        # Create adapter and test context
        adapter = BedrockAdapter()
        print("✅ BedrockAdapter created successfully")
        
        # Create test context
        context_data = adapter.create_new_context("You are a helpful AI assistant.")
        print("✅ Context created successfully")
        
        # Test simple generation
        print("🧪 Testing simple generation...")
        response, updated_context = await adapter.generate_with_context(
            context_data=context_data,
            new_message="Hello! Please respond with just 'Hi there!' to confirm you're working.",
            model="us.anthropic.claude-3-5-haiku-20241022-v1:0",
            temperature=0.1,
            max_tokens=50
        )
        
        print(f"✅ Response received: {response}")
        print(f"✅ Context updated: {len(updated_context['messages'])} messages")
        
        return True
        
    except ImportError as e:
        print(f"❌ Import error: {e}")
        print("Make sure you're running from the backend directory")
        return False
    except Exception as e:
        print(f"❌ Bedrock test failed: {e}")
        return False

if __name__ == "__main__":
    print("🧪 Testing AWS Bedrock Bearer Token Integration")
    print("=" * 50)
    
    success = asyncio.run(test_bedrock())
    
    if success:
        print("\n🎉 Bedrock integration test PASSED!")
        print("You can now use 'bedrock' provider in your chat nodes.")
    else:
        print("\n💥 Bedrock integration test FAILED!")
        print("Check your AWS_BEARER_TOKEN_BEDROCK configuration.")
        sys.exit(1)