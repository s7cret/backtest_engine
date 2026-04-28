import math
def round_to_step(value:float, step:float|None, mode:str='nearest')->float:
    if not step: return value
    x=value/step
    if mode=='floor': return math.floor(x)*step
    if mode=='ceil': return math.ceil(x)*step
    return round(x)*step
