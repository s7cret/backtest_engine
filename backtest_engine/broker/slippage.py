def slippage_value(price:float, side:str, position_effect:str, raw:float, typ:str, mintick:float|None)->float:
    if raw == 0: return 0.0
    val = raw*(mintick or 1.0) if typ=='tick' else raw if typ=='price' else price*raw/100.0
    worse_up = side=='buy'
    return val if worse_up else -val
