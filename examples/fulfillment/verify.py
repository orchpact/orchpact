"""Independent, bounded feasibility checks. Does not read the reference solution."""
from collections import Counter
import json
from pathlib import Path

from orchpact.kernel.contracts import validate

HERE=Path(__file__).resolve().parent


def check_plan(original,candidate):
    operation=json.loads((HERE/'workflow.json').read_text())['operations']['fulfill']
    try:validate(original,operation['input_schema'])
    except ValueError:return dict(status='INCONCLUSIVE',violations=[],uncertainty='invalid_input_schema')
    for field in ('warehouses','orders','carriers'):
        ids=[row['id'] for row in original[field]]
        if len(ids)!=len(set(ids)):return dict(status='INCONCLUSIVE',violations=[],uncertainty='ambiguous_input_ids')
    warehouse_ids={row['id'] for row in original['warehouses']}
    carrier_ids={row['id'] for row in original['carriers']}
    lanes=[(row['warehouse_id'],row['region'],row['carrier']) for row in original['routes']]
    if len(lanes)!=len(set(lanes)) or any(row['warehouse_id'] not in warehouse_ids or row['carrier'] not in carrier_ids for row in original['routes']) or any(row['warehouse_id'] not in warehouse_ids or row['day']>original['horizon_days'] for row in original['inbounds']):
        return dict(status='INCONCLUSIVE',violations=[],uncertainty='ambiguous_input_routes_or_receipts')
    schema=next(node for node in operation['execution_plan']['nodes'] if node['key']=='allocation')['outputs']['allocation.json']
    try:validate(candidate,schema)
    except ValueError as error:return dict(status='FAIL',violations=[dict(code='schema',path='shipments',message=str(error))])
    warehouses={row['id']:row for row in original['warehouses']}
    orders={row['id']:row for row in original['orders']}
    carriers={row['id']:row for row in original['carriers']}
    routes={(row['warehouse_id'],row['region'],row['carrier']):row for row in original['routes']}
    horizon=original['horizon_days']
    violations=[]
    def reject(code,path,message):
        if len(violations)<32:violations.append(dict(code=code,path=path,message=message))
    used=set();shipped=Counter();stock_used=Counter();warehouse_load=Counter();carrier_load=Counter()
    costs=Counter();arrivals=Counter();total=0
    for index,row in enumerate(candidate['shipments']):
        path=f'shipments[{index}]'
        if row['shipment_id'] in used:reject('duplicate_shipment',path,'Shipment IDs must be unique')
        used.add(row['shipment_id'])
        order=orders.get(row['order_id']);warehouse=warehouses.get(row['warehouse_id']);carrier=carriers.get(row['carrier'])
        if not order or not warehouse or not carrier:
            reject('unknown_reference',path,'Unknown order, warehouse or carrier');continue
        quantity=row['quantity'];day=row['ship_day'];sku=row['sku']
        if sku not in order['items'] or sku not in warehouse['stock']:
            reject('unknown_sku',path,'SKU must belong to the order and warehouse');continue
        if not 1<=day<=horizon:reject('ship_day',path,'Ship day is outside the planning horizon');continue
        route=routes.get((warehouse['id'],order['region'],carrier['id']))
        if not route:reject('route_missing',path,'No declared route');continue
        arrival=day+route['transit_days']
        if arrival>order['deadline']:reject('deadline',path,'Arrival exceeds the order deadline')
        shipped[(order['id'],sku)]+=quantity
        stock_used[(warehouse['id'],sku,day)]+=quantity
        warehouse_load[(warehouse['id'],day)]+=quantity
        carrier_load[(carrier['id'],day)]+=quantity
        arrivals[order['id']]=max(arrivals[order['id']],arrival)
        price=quantity*route['unit_cost_cents'];total+=price;costs[warehouse['id']]+=price
    for order in original['orders']:
        for sku,quantity in order['items'].items():
            if shipped[(order['id'],sku)]!=quantity:reject('order_quantity',order['id']+'.'+sku,'Every required order item must be fulfilled exactly once in total')
    for warehouse in original['warehouses']:
        for sku,initial in warehouse['stock'].items():
            available=initial
            for day in range(1,horizon+1):
                available+=sum(row['quantity'] for row in original['inbounds'] if row['warehouse_id']==warehouse['id'] and row['sku']==sku and row['day']==day)
                available-=stock_used[(warehouse['id'],sku,day)]
                if available<0:reject('inventory',f'{warehouse["id"]}.{sku}.day{day}','Cannot ship stock before it is available')
        for day in range(1,horizon+1):
            if warehouse_load[(warehouse['id'],day)]>warehouse['daily_capacity']:reject('warehouse_capacity',f'{warehouse["id"]}.day{day}','Warehouse daily capacity exceeded')
    for carrier in original['carriers']:
        for day in range(1,horizon+1):
            if carrier_load[(carrier['id'],day)]>carrier['daily_capacity']:reject('carrier_capacity',f'{carrier["id"]}.day{day}','Carrier daily capacity exceeded')
    if total>original['budget_cents']:reject('budget','total_cost_cents','Transport cost exceeds the frozen budget')
    return dict(status='FAIL' if violations else 'INCONCLUSIVE' if original['rate_confirmation']!='confirmed' else 'PASS',violations=violations,
        total_cost_cents=total,cost_by_warehouse=[dict(warehouse_id=key,cost_cents=costs[key]) for key in sorted(warehouses)],
        deliveries=[dict(order_id=key,units=sum(shipped[(key,sku)] for sku in orders[key]['items']),arrival_day=arrivals[key]) for key in sorted(orders)])


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,default=HERE/'input.json')
    parser.add_argument('--candidate',type=Path,required=True)
    args=parser.parse_args();result=check_plan(json.loads(args.input.read_text()),json.loads(args.candidate.read_text()))
    print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(0 if result['status']=='PASS' else 2 if result['status']=='FAIL' else 3)
