def quicksort(arr, key=lambda x: x):
    if len(arr) <= 1:
        return arr.copy()
    pivot = arr[len(arr) // 2]
    left = [x for x in arr if key(x) < key(pivot)]
    middle = [x for x in arr if key(x) == key(pivot)]
    right = [x for x in arr if key(x) > key(pivot)]
    return (
        quicksort(left, key)
        + middle
        + quicksort(right, key)
    )