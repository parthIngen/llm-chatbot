import asyncio
import json
from app.llm.intent_detector import IntentDetector

async def test_all_transporter_queries():
    detector = IntentDetector()
    
    # Test 1: Unique Transporters only
    msg1 = "Show total number of unique transporters used in Aug 2026"
    res1 = await detector.detect(message=msg1, history=[], session_id="test-transporters-only")
    entities1 = res1.get("entities", {})
    print("--- Test 1: Unique Transporters Only ---")
    print(json.dumps(entities1, indent=2))
    assert "groupBy" not in entities1 or not entities1["groupBy"], "groupBy should NOT be present for unique transporters query"
    assert entities1.get("metrics") == [{"field": "Transporter", "function": "countDistinct", "alias": "uniqueTransporterCount"}]
    print("Test 1 Passed!")

    # Test 2: Unique Vehicles and Transporters
    msg2 = "Count total unique vehicles and distinct transporters for April 2026"
    res2 = await detector.detect(message=msg2, history=[], session_id="test-vehicles-and-transporters")
    entities2 = res2.get("entities", {})
    print("\n--- Test 2: Unique Vehicles and Transporters ---")
    print(json.dumps(entities2, indent=2))
    assert entities2.get("groupBy") == ["Transporter"]
    assert entities2.get("metrics") == [{"field": "vehicleNo", "function": "countDistinct", "alias": "uniqueVehicleCount"}]
    print("Test 2 Passed!")

if __name__ == "__main__":
    asyncio.run(test_all_transporter_queries())
