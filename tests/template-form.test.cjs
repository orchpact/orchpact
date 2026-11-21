const test=require('node:test'),assert=require('node:assert/strict');
const form=require('../src/orchpact/static/kernel/template-form.js');

const schema={type:'object',additionalProperties:false,required:['name','limit','enabled','tags'],properties:{
 name:{type:'string',default:'example'},limit:{type:'integer',default:2},enabled:{type:'boolean'},tags:{type:'array',items:{type:'string'}},optional:{type:'string'},
 nested:{type:'object',additionalProperties:false,default:{},properties:{label:{type:'string',default:'inside'},deep:{type:'object',additionalProperties:false,default:{},properties:{value:{type:'number',default:1.5}}}}},
 mode:{type:'string',enum:['safe','strict'],default:'strict'},
}};
function root(values){
 const controls=Object.entries(values).map(([path,value])=>({dataset:{path},type:typeof value==='boolean'?'checkbox':'text',value:checked(value),checked:value===true,multiple:Array.isArray(value),selectedOptions:Array.isArray(value)?value.map(value=>({value:String(value)})):[]}));
 function checked(value){return Array.isArray(value)?'':typeof value==='boolean'?'':String(value??'');}
 return {querySelectorAll:selector=>selector==='[data-path]'?controls:[]};
}

test('递归默认值与服务端结构一致且不修改 Schema',()=>{
 const before=JSON.stringify(schema),value=form.defaults(schema);
 assert.deepEqual(value,{name:'example',limit:2,mode:'strict',nested:{label:'inside',deep:{value:1.5}}});
 assert.equal(JSON.stringify(schema),before);assert.notEqual(value.nested,schema.properties.nested.default);
});

test('标量解析拒绝非有限数字、非整数和模糊布尔值',()=>{
 assert.equal(form.parseScalar('2.5',{type:'number'},'ratio'),2.5);
 assert.equal(form.parseScalar('2',{type:'integer'},'limit'),2);
 assert.equal(form.parseScalar('false',{type:'boolean'},'enabled'),false);
 for(const [value,kind] of [['NaN','number'],['Infinity','number'],['1.2','integer'],['yes','boolean']])assert.throws(()=>form.parseScalar(value,{type:kind},'field'));
});

test('控件读取支持嵌套、布尔、数组和可选空值',()=>{
 const value=form.read(root({name:'demo',limit:'3',enabled:true,tags:'one\ntwo','nested.label':'custom','nested.deep.value':'2.5',optional:'',mode:'safe'}),schema);
 assert.deepEqual(value,{name:'demo',limit:3,enabled:true,tags:['one','two'],nested:{label:'custom',deep:{value:2.5}},mode:'safe'});
});

test('可选布尔与数组可省略，非法数组数字不会被静默转换',()=>{
 const custom={type:'object',additionalProperties:false,properties:{flag:{type:'boolean'},numbers:{type:'array',items:{type:'number'}}}};
 assert.deepEqual(form.read(root({flag:'',numbers:''}),custom),{});
 assert.throws(()=>form.read(root({flag:'',numbers:'1\nnope'}),custom),/numbers\[1\]/);
});

test('错误路径映射保留嵌套参数并识别根错误',()=>{
 assert.equal(form.errorPath('输入/交付不符合结构约定：字段nested.label，规则minLength'),'nested.label');
 assert.equal(form.errorPath('模板参数无效：字段parameters.tags[]，规则type'),'tags[]');
 assert.equal(form.errorPath('输入/交付不符合结构约定：字段<root>，规则required'),'');
 assert.equal(form.errorPath('其他错误'),'');
});

test('渲染转义作者文本并输出有效控件属性和只读计划',()=>{
 const malicious={type:'object',additionalProperties:false,required:['name'],properties:{name:{type:'string',title:'<script>',description:'"unsafe"',pattern:'^[a-z]+$',minLength:1}}};
 const html=form.render(malicious,{name:'<img>'});
 assert.ok(!html.includes('<script>'));assert.ok(html.includes('&lt;script&gt;'));assert.ok(html.includes('pattern="^[a-z]+$" required'));
 const preview=form.previewHtml({sha256:'abc',definition:{operations:{generate:{instruction:'Deliver',output_file:'result.json',execution_plan:{nodes:[{key:'deliver',kind:'agent',depends_on:[],outputs:{'result.json':{}}}]}}}}});
 assert.ok(preview.includes('deliver'));assert.ok(preview.includes('result.json'));assert.ok(preview.includes('data-publish-template'));
});
