import sys
import pathlib

# Add workspace root to import path
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from prologix_lr700_test import parse_measurement

def run_tests():
    test_cases = [
        ("+000.06 MOHM R", "R", 0.06e-3),
        ("+000 .06 MOHM R", "R", 0.06e-3),
        ("-001.23 OHM X", "X", -1.23),
        ("-001 23 OHM X", "X", -1.23),
        (" 12.34 KOHM R ", "R", 12.34e3),
        (" +000   .05   MOHM   R ", "R", 0.05e-3),
    ]
    
    print("Running parser robustness tests...")
    all_passed = True
    for response, expected_kind, expected_ohms in test_cases:
        try:
            m = parse_measurement(response, expected_kind)
            assert abs(m.value_ohms - expected_ohms) < 1e-9, f"Expected {expected_ohms}, got {m.value_ohms}"
            print(f"PASS: {response!r} -> {m.value_ohms} ohms ({m.raw_value} {m.raw_unit} {m.kind})")
        except Exception as e:
            print(f"FAIL: {response!r} | Error: {e}")
            all_passed = False
            
    if all_passed:
        print("\nAll parser robustness tests passed successfully!")
    else:
        print("\nSome tests failed.")

if __name__ == "__main__":
    run_tests()
