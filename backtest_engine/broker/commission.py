def calculate_commission(price:float, qty:float, commission_type:str, commission_value:float)->float:
    if commission_type=='none': return 0.0
    if commission_type=='percent': return abs(price*qty)*commission_value/100.0
    if commission_type=='fixed_per_order': return commission_value
    if commission_type=='fixed_per_contract': return abs(qty)*commission_value
    raise ValueError(f'unknown commission_type {commission_type}')
