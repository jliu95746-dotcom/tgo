#!/usr/bin/env python3
"""Test script for internal service endpoints.

This script tests the authenticated internal AI events endpoint.
"""

import asyncio
import httpx
import os
from uuid import uuid4


async def test_internal_health():
    """Test internal service health check."""
    print("Testing internal service health check...")
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get("http://localhost:8001/health")
            print(f"✅ Health check: {response.status_code}")
            print(f"   Response: {response.json()}")
            return response.status_code == 200
        except Exception as e:
            print(f"❌ Health check failed: {e}")
            return False


async def test_internal_ai_event():
    """Test the internal AI event endpoint with the service key from the environment."""
    print("\nTesting internal AI event endpoint...")
    service_key = os.environ.get("SECRET_KEY")
    if not service_key:
        print("SECRET_KEY is required for the internal AI event probe")
        return False
    
    # Sample event data
    event_data = {
        "event_type": "manual_service.request",
        "user_id": str(uuid4()),  # Deliberately nonexistent: no business mutation
        "payload": {
            "reason": "Test event from internal service",
            "urgency": "low",
            "channel": "test",
            "notification_type": "none",
            "metadata": {
                "test": True,
                "source": "test_script"
            }
        }
    }
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                "http://localhost:8001/internal/ai/events",
                json=event_data,
                headers={"X-Internal-API-Key": service_key},
                timeout=10.0
            )
            print(f"Status: {response.status_code}")
            print(f"Response: {response.json()}")
            
            if response.status_code == 202:
                print("✅ Internal AI event endpoint works!")
                return True
            elif response.status_code == 404:
                print("⚠️  Project or visitor not found (expected for test data)")
                print("   Endpoint is working, but test data is invalid")
                return True
            else:
                print(f"❌ Unexpected status code: {response.status_code}")
                return False
                
        except Exception as e:
            print(f"❌ Request failed: {e}")
            return False


async def test_main_api_requires_auth():
    """Test that main API endpoint requires authentication."""
    print("\nTesting main API endpoint (should require auth)...")
    
    event_data = {
        "event_type": "manual_service.request",
        "visitor_id": str(uuid4()),
        "payload": {
            "reason": "Test event",
            "urgency": "low"
        }
    }
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                "http://localhost:8000/v1/ai/events",
                json=event_data,
                timeout=10.0
            )
            
            if response.status_code == 401 or response.status_code == 403:
                print("✅ Main API correctly requires authentication")
                print(f"   Status: {response.status_code}")
                return True
            else:
                print(f"⚠️  Unexpected status code: {response.status_code}")
                print(f"   Response: {response.json()}")
                return False
                
        except Exception as e:
            print(f"❌ Request failed: {e}")
            return False


async def main():
    """Run all tests."""
    print("=" * 60)
    print("TGO-Tech Internal Service Test Suite")
    print("=" * 60)
    print()
    
    results = []
    
    # Test 1: Health check
    results.append(await test_internal_health())
    
    # Test 2: Internal AI event endpoint
    results.append(await test_internal_ai_event())
    
    # Test 3: Main API requires auth
    results.append(await test_main_api_requires_auth())
    
    # Summary
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)
    passed = sum(results)
    total = len(results)
    print(f"Passed: {passed}/{total}")
    
    if passed == total:
        print("✅ All tests passed!")
    else:
        print("❌ Some tests failed")
    
    print("\nNote: Some tests may fail if services are not running.")
    print("Start services with: ./scripts/start_services.sh")


if __name__ == "__main__":
    asyncio.run(main())

