RANGES = [("today", "Today"), ("yesterday", "Yesterday"), ("7d", "7 days"), ("week", "This week"),
          ("month", "This month"), ("lastmonth", "Last month"), ("30d", "30 days")]


def ranges(request):
    return {"ranges": RANGES}
