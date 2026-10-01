import json
from analyze_transporters import data

print("Total raw entries in list:", len(data))

transporters_raw = [item["Transporter"] for item in data]
transporters_non_empty = [t for t in transporters_raw if t is not None and t.strip() != ""]

print("Distinct raw non-empty Transporter string values:", len(set(transporters_non_empty)))
print("Distinct case-insensitive non-empty Transporter names:", len(set(t.strip().upper() for t in transporters_non_empty)))

# Check top transporters by vehicle count
sorted_data = sorted(data, key=lambda x: x["uniqueVehicleCount"], reverse=True)
print("\nTop 10 entries by uniqueVehicleCount:")
for item in sorted_data[:10]:
    print(f"  - {repr(item['Transporter'])}: {item['uniqueVehicleCount']} vehicles")

# Check null / unassigned total
unassigned_vehicles = sum(item["uniqueVehicleCount"] for item in data if item["Transporter"] is None or item["Transporter"].strip() == "")
print(f"\nUnassigned/Null/Empty transporter vehicle count: {unassigned_vehicles}")

assigned_vehicles = sum(item["uniqueVehicleCount"] for item in data if item["Transporter"] is not None and item["Transporter"].strip() != "")
print(f"Assigned transporter vehicle count: {assigned_vehicles}")
print(f"Grand Total vehicle count: {assigned_vehicles + unassigned_vehicles}")
